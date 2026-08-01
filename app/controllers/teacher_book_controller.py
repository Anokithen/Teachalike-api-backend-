"""Approved-teacher book management with server-owned attribution and media."""

import re
from uuid import uuid4

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models.asset_model import (
    Asset,
    BOOK_COVER_IMAGE,
    BOOK_ILLUSTRATION,
    BOOK_VIDEO,
    STATUS_COMPLETED,
)
from app.models.book_like_model import BookLike
from app.models.book_model import Book
from app.models.book_view_model import BookView
from app.models.reading_session_model import ReadingSession
from app.security import book_creation_attempts
from app.services.book_games import create_default_mini_games
from app.services.book_management_service import (
    asset_reference,
    book_asset_references,
    cleanup_references,
    request_book_data,
    validate_book_payload,
    validate_media_files,
)
from app.services.cloudinary_path_service import (
    get_book_image_folder,
    get_book_video_folder,
)
from app.services.cloudinary_service import CloudinaryServiceError, upload_asset

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _owned_book(book_id):
    book = db.session.get(Book, book_id)
    if book is None:
        return None, (jsonify({"error": "Book not found."}), 404)
    if book.created_by_account_id != current_user.id:
        return None, (jsonify({"error": "You cannot manage another teacher's book."}), 403)
    return book, None


def _upload(upload, *, folder, category, book, kind, index=None):
    suffix = f"{kind}_{index}_{uuid4().hex}" if index is not None else f"{kind}_{uuid4().hex}"
    metadata = upload_asset(
        upload,
        folder,
        resource_type="video" if category == BOOK_VIDEO else "image",
        public_id=f"{folder}/{suffix}",
        tags=[category.lower(), f"book_{book.id}", f"teacher_{current_user.id}"],
        context={"book_id": str(book.id), "owner_account_id": str(current_user.id)},
    )
    db.session.add(
        Asset.from_cloudinary_metadata(
            metadata,
            category=category,
            owner_user_id=current_user.id,
            book_id=book.id,
            active_slot=f"book:{book.id}:{kind}" if index is None else None,
            status=STATUS_COMPLETED,
        )
    )
    return metadata


def _status_for_validation(message):
    return 413 if "exceeds" in message else 415


def create_book():
    request_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if request_key and not IDEMPOTENCY_PATTERN.fullmatch(request_key):
        return jsonify({"error": "Idempotency-Key must be 8-64 letters, numbers, underscores, or hyphens."}), 400
    if request_key:
        existing = Book.query.filter_by(
            created_by_account_id=current_user.id,
            creation_request_id=request_key,
        ).first()
        if existing:
            return jsonify({"message": "Book already created.", "book": existing.to_dict(True)}), 200

    blocked, retry_after = book_creation_attempts.blocked(
        f"teacher-book:{current_user.id}", 20, 3600
    )
    if blocked:
        response = jsonify({"error": "Too many book creation attempts. Please try again later."})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429
    book_creation_attempts.record_failure(f"teacher-book:{current_user.id}", 3600)

    data = request_book_data(request)
    errors, values = validate_book_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400
    try:
        cover, illustrations, video = validate_media_files(request)
    except ValueError as exc:
        return jsonify({"errors": [str(exc)]}), _status_for_validation(str(exc))

    uploaded = []
    try:
        book = Book(
            **values,
            created_by_account_id=current_user.id,
            creator_name_snapshot=current_user.name,
            creation_request_id=request_key or None,
        )
        db.session.add(book)
        db.session.flush()
        create_default_mini_games(book)

        image_folder = get_book_image_folder(current_user.id, book.id, book.title)
        if cover:
            metadata = _upload(
                cover, folder=image_folder, category=BOOK_COVER_IMAGE,
                book=book, kind="cover"
            )
            uploaded.append(metadata)
            book.cover_image_url = metadata["secure_url"]
        if illustrations:
            urls = []
            for index, illustration in enumerate(illustrations, start=1):
                metadata = _upload(
                    illustration, folder=image_folder, category=BOOK_ILLUSTRATION,
                    book=book, kind="illustration", index=index
                )
                uploaded.append(metadata)
                urls.append(metadata["secure_url"])
            book.image_urls = urls
        if video:
            folder = get_book_video_folder(current_user.id, current_user.id, book.id, book.title)
            metadata = _upload(
                video, folder=folder, category=BOOK_VIDEO, book=book, kind="video"
            )
            uploaded.append(metadata)
            book.video_url = metadata["secure_url"]
        db.session.commit()
        return jsonify({
            "message": "Book created successfully.",
            "book": book.to_dict(include_content=True),
        }), 201
    except IntegrityError:
        db.session.rollback()
        cleanup_references([{
            "public_id": item.get("public_id"),
            "resource_type": item.get("resource_type"),
            "delivery_type": item.get("delivery_type"),
        } for item in uploaded])
        if request_key:
            existing = Book.query.filter_by(
                created_by_account_id=current_user.id,
                creation_request_id=request_key,
            ).first()
            if existing:
                return jsonify({"message": "Book already created.", "book": existing.to_dict(True)}), 200
        return jsonify({"error": "The book conflicted with another request."}), 409
    except CloudinaryServiceError:
        db.session.rollback()
        cleanup_references(uploaded)
        return jsonify({"error": "Book media upload failed."}), 503
    except Exception:
        db.session.rollback()
        cleanup_references(uploaded)
        return jsonify({"error": "The book could not be created."}), 500


def _engagement_subqueries():
    views = db.session.query(
        BookView.book_id.label("book_id"),
        func.count(BookView.id).label("total_views"),
    ).group_by(BookView.book_id).subquery()
    reads = db.session.query(
        ReadingSession.book_id.label("book_id"),
        func.count(ReadingSession.id).label("total_reads"),
    ).group_by(ReadingSession.book_id).subquery()
    likes = db.session.query(
        BookLike.book_id.label("book_id"),
        func.count(BookLike.id).label("likes"),
    ).group_by(BookLike.book_id).subquery()
    return views, reads, likes


def list_books():
    views, reads, likes = _engagement_subqueries()
    rows = db.session.query(
        Book,
        func.coalesce(views.c.total_views, 0),
        func.coalesce(reads.c.total_reads, 0),
        func.coalesce(likes.c.likes, 0),
    ).outerjoin(views, views.c.book_id == Book.id).outerjoin(
        reads, reads.c.book_id == Book.id
    ).outerjoin(likes, likes.c.book_id == Book.id).filter(
        Book.created_by_account_id == current_user.id
    ).order_by(Book.updated_at.desc(), Book.id.desc()).all()
    return jsonify({"books": [{
        **book.to_dict(),
        "total_views": int(view_count or 0),
        "total_reads": int(read_count or 0),
        "likes": int(like_count or 0),
    } for book, view_count, read_count, like_count in rows]}), 200


def get_book(book_id):
    book, error = _owned_book(book_id)
    if error:
        return error
    return jsonify({"book": book.to_dict(include_content=True)}), 200


def update_book(book_id):
    book, error = _owned_book(book_id)
    if error:
        return error
    data = request_book_data(request)
    errors, values = validate_book_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400
    try:
        cover, illustrations, video = validate_media_files(request)
    except ValueError as exc:
        return jsonify({"errors": [str(exc)]}), _status_for_validation(str(exc))

    uploaded = []
    old_refs = []
    try:
        for field, value in values.items():
            setattr(book, field, value)
        image_folder = get_book_image_folder(current_user.id, book.id, book.title)
        if cover:
            old = Asset.query.filter_by(
                book_id=book.id, asset_category=BOOK_COVER_IMAGE
            ).all()
            old_refs.extend(asset_reference(item) for item in old)
            for item in old:
                db.session.delete(item)
            metadata = _upload(cover, folder=image_folder, category=BOOK_COVER_IMAGE, book=book, kind="cover")
            uploaded.append(metadata)
            book.cover_image_url = metadata["secure_url"]
        if illustrations:
            old = Asset.query.filter_by(
                book_id=book.id, asset_category=BOOK_ILLUSTRATION
            ).all()
            old_refs.extend(asset_reference(item) for item in old)
            for item in old:
                db.session.delete(item)
            urls = []
            for index, illustration in enumerate(illustrations, start=1):
                metadata = _upload(
                    illustration, folder=image_folder, category=BOOK_ILLUSTRATION,
                    book=book, kind="illustration", index=index
                )
                uploaded.append(metadata)
                urls.append(metadata["secure_url"])
            book.image_urls = urls
        if video:
            old = Asset.query.filter_by(book_id=book.id, asset_category=BOOK_VIDEO).all()
            old_refs.extend(asset_reference(item) for item in old)
            for item in old:
                db.session.delete(item)
            folder = get_book_video_folder(current_user.id, current_user.id, book.id, book.title)
            metadata = _upload(video, folder=folder, category=BOOK_VIDEO, book=book, kind="video")
            uploaded.append(metadata)
            book.video_url = metadata["secure_url"]
        db.session.commit()
        cleanup_references(old_refs)
        return jsonify({"message": "Book updated successfully.", "book": book.to_dict(True)}), 200
    except (SQLAlchemyError, CloudinaryServiceError):
        db.session.rollback()
        cleanup_references(uploaded)
        return jsonify({"error": "The book could not be updated."}), 503
    except Exception:
        db.session.rollback()
        cleanup_references(uploaded)
        return jsonify({"error": "The book could not be updated."}), 500


def delete_book(book_id):
    book, error = _owned_book(book_id)
    if error:
        return error
    if ReadingSession.query.filter_by(book_id=book.id).first():
        return jsonify({"error": "This book cannot be deleted because it has reading sessions."}), 409
    references = book_asset_references(book.id)
    try:
        Asset.query.filter_by(book_id=book.id).delete(synchronize_session=False)
        db.session.delete(book)
        db.session.commit()
        cleanup_references(references)
        return jsonify({"message": "Book deleted successfully."}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "The book could not be deleted."}), 500
