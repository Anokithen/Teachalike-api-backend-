"""Approved-teacher book management with server-owned attribution and media."""

import re

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models.asset_model import (
    Asset,
    BOOK_COVER_IMAGE,
    BOOK_IMAGE,
    BOOK_ILLUSTRATION,
    BOOK_VIDEO,
    STATUS_COMPLETED,
    STATUS_DELETED,
    TEACHER_BOOK_AUDIO,
)
from app.models.book_like_model import BookLike
from app.models.book_model import Book
from app.models.book_view_model import BookView
from app.models.reading_session_model import ReadingSession
from app.security import book_creation_attempts
from app.services.book_games import create_default_mini_games, ensure_book_games
from app.services.book_management_service import (
    asset_reference,
    cleanup_references,
    BookAssetCleanupError,
    delete_book_with_registered_assets,
    ensure_book_asset_root,
    request_book_data,
    validate_book_payload,
    validate_media_files,
)
from app.services.cloudinary_path_service import (
    get_book_images_folder_from_root,
    get_book_video_folder_from_root,
    get_teacher_book_audio_folder_from_root,
)
from app.services.cloudinary_service import CloudinaryServiceError, upload_asset
from app.utils import utc_now

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _owned_book(book_id):
    book = db.session.get(Book, book_id)
    if book is None:
        return None, (jsonify({"error": "Book not found."}), 404)
    if book.created_by_account_id != current_user.id:
        return None, (jsonify({"error": "You cannot manage another teacher's book."}), 403)
    return book, None


def _upload(
    upload,
    *,
    folder,
    category,
    book,
    public_name,
    slot,
    overwrite=False,
):
    is_audio = category == TEACHER_BOOK_AUDIO
    metadata = upload_asset(
        upload,
        folder,
        resource_type="video" if category in {BOOK_VIDEO, TEACHER_BOOK_AUDIO} else "image",
        public_id=f"{folder}/{public_name}",
        overwrite=overwrite,
        delivery_type="authenticated" if is_audio else "upload",
        tags=[category.lower(), f"book_{book.id}", f"teacher_{current_user.id}"],
        context={"book_id": str(book.id), "owner_account_id": str(current_user.id)},
    )
    db.session.add(
        Asset.from_cloudinary_metadata(
            metadata,
            category=category,
            owner_user_id=current_user.id,
            book_id=book.id,
            active_slot=f"book:{book.id}:{slot}",
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
        cover, illustrations, video, teacher_audio = validate_media_files(request)
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
        asset_root = ensure_book_asset_root(book)
        create_default_mini_games(book)

        image_folder = get_book_images_folder_from_root(asset_root)
        if cover:
            metadata = _upload(
                cover, folder=image_folder, category=BOOK_IMAGE,
                book=book, public_name="cover", slot="cover"
            )
            uploaded.append(metadata)
            book.cover_image_url = metadata["secure_url"]
        if illustrations:
            urls = []
            for index, illustration in enumerate(illustrations, start=1):
                metadata = _upload(
                    illustration, folder=image_folder, category=BOOK_IMAGE,
                    book=book, public_name=f"picture_{index:02d}",
                    slot=f"picture:{index:02d}"
                )
                uploaded.append(metadata)
                urls.append(metadata["secure_url"])
            book.image_urls = urls
        if video:
            folder = get_book_video_folder_from_root(asset_root)
            metadata = _upload(
                video, folder=folder, category=BOOK_VIDEO, book=book,
                public_name="video_01", slot="video:01"
            )
            uploaded.append(metadata)
            book.video_url = metadata["secure_url"]
        if teacher_audio:
            folder = get_teacher_book_audio_folder_from_root(asset_root)
            metadata = _upload(
                teacher_audio, folder=folder, category=TEACHER_BOOK_AUDIO,
                book=book, public_name="voice_audio_teacher", slot="teacher_audio"
            )
            uploaded.append(metadata)
        db.session.commit()
        try:
            ensure_book_games(book.id, config=current_app.config)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Teacher book mini-game generation failed safely for book_id=%s", book.id
            )
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
        cover, illustrations, video, teacher_audio = validate_media_files(request)
    except ValueError as exc:
        return jsonify({"errors": [str(exc)]}), _status_for_validation(str(exc))

    uploaded = []
    old_refs = []
    try:
        for field, value in values.items():
            setattr(book, field, value)
        asset_root = ensure_book_asset_root(book)
        image_folder = get_book_images_folder_from_root(asset_root)
        if cover:
            old = Asset.query.filter(
                Asset.book_id == book.id,
                Asset.asset_category.in_((BOOK_COVER_IMAGE, BOOK_IMAGE)),
                Asset.active_slot == f"book:{book.id}:cover",
                Asset.deleted_at.is_(None),
            ).all()
            old_ids = {item.cloudinary_public_id for item in old}
            for item in old:
                item.active_slot = None
                item.status = STATUS_DELETED
                item.deleted_at = utc_now()
            metadata = _upload(
                cover, folder=image_folder, category=BOOK_IMAGE, book=book,
                public_name="cover", slot="cover", overwrite=True
            )
            old_refs.extend(
                asset_reference(item) for item in old
                if item.cloudinary_public_id != metadata["public_id"]
            )
            if metadata["public_id"] not in old_ids:
                uploaded.append(metadata)
            book.cover_image_url = metadata["secure_url"]
        if illustrations:
            old = Asset.query.filter(
                Asset.book_id == book.id,
                or_(
                    Asset.asset_category == BOOK_ILLUSTRATION,
                    and_(
                        Asset.asset_category == BOOK_IMAGE,
                        Asset.active_slot.like(f"book:{book.id}:picture:%"),
                    ),
                ),
                Asset.deleted_at.is_(None),
            ).all()
            old_ids = {item.cloudinary_public_id for item in old}
            for item in old:
                item.active_slot = None
                item.status = STATUS_DELETED
                item.deleted_at = utc_now()
            urls = []
            new_ids = set()
            for index, illustration in enumerate(illustrations, start=1):
                metadata = _upload(
                    illustration, folder=image_folder, category=BOOK_IMAGE,
                    book=book, public_name=f"picture_{index:02d}",
                    slot=f"picture:{index:02d}", overwrite=True
                )
                new_ids.add(metadata["public_id"])
                if metadata["public_id"] not in old_ids:
                    uploaded.append(metadata)
                urls.append(metadata["secure_url"])
            old_refs.extend(
                asset_reference(item) for item in old
                if item.cloudinary_public_id not in new_ids
            )
            book.image_urls = urls
        if video:
            old = Asset.query.filter_by(
                book_id=book.id, asset_category=BOOK_VIDEO, deleted_at=None,
            ).all()
            old_ids = {item.cloudinary_public_id for item in old}
            for item in old:
                item.active_slot = None
                item.status = STATUS_DELETED
                item.deleted_at = utc_now()
            folder = get_book_video_folder_from_root(asset_root)
            metadata = _upload(
                video, folder=folder, category=BOOK_VIDEO, book=book,
                public_name="video_01", slot="video:01", overwrite=True
            )
            old_refs.extend(asset_reference(item) for item in old if item.cloudinary_public_id != metadata["public_id"])
            if metadata["public_id"] not in old_ids:
                uploaded.append(metadata)
            book.video_url = metadata["secure_url"]
        if teacher_audio:
            old = Asset.query.filter_by(
                book_id=book.id, asset_category=TEACHER_BOOK_AUDIO,
                active_slot=f"book:{book.id}:teacher_audio", deleted_at=None,
            ).all()
            old_ids = {item.cloudinary_public_id for item in old}
            for item in old:
                item.active_slot = None
                item.status = STATUS_DELETED
                item.deleted_at = utc_now()
            folder = get_teacher_book_audio_folder_from_root(asset_root)
            metadata = _upload(
                teacher_audio, folder=folder, category=TEACHER_BOOK_AUDIO,
                book=book, public_name="voice_audio_teacher",
                slot="teacher_audio", overwrite=True
            )
            old_refs.extend(asset_reference(item) for item in old if item.cloudinary_public_id != metadata["public_id"])
            if metadata["public_id"] not in old_ids:
                uploaded.append(metadata)
        db.session.commit()
        cleanup_references(old_refs)
        try:
            ensure_book_games(book.id, config=current_app.config)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Updated teacher book mini-game generation failed safely for book_id=%s", book.id
            )
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
    try:
        delete_book_with_registered_assets(book)
        return jsonify({"message": "Book deleted successfully."}), 200
    except BookAssetCleanupError:
        return jsonify({"error": "Book asset cleanup is incomplete. Please retry."}), 503
    except Exception:
        db.session.rollback()
        return jsonify({"error": "The book could not be deleted."}), 500
