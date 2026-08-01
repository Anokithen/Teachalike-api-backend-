from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from app.extensions import db
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from app.models.parent_model import Parent, ROLE_PARENT, ROLE_TEACHER
from app.models.child_model import Child
from app.models.book_model import Book
from app.models.reading_session_model import ReadingSession
from app.models.book_view_model import BookView
from app.models.book_like_model import BookLike
from app.models.asset_model import Asset, USER_PROFILE_IMAGE
from app.models.teacher_application_model import (
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    TeacherApplication,
    VALID_TEACHER_TYPES,
    VALID_APPROVAL_STATUSES,
)
from app.utils import utc_now
from app.services.book_games import create_default_mini_games
from app.services.account_cleanup_service import (
    collect_account_asset_refs,
    remove_account_asset_ledger_rows,
    schedule_account_asset_cleanup,
)
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    upload_asset,
    upload_book_media,
    validate_upload_size,
    validate_uploaded_file,
)
from app.services.cloudinary_path_service import get_user_profile_folder
from app.services.gemini_service import GeminiError, generate_book_draft as generate_gemini_book_draft
from app.services.groq_service import GroqError, generate_book_draft as generate_groq_book_draft
from app.services.nvidia_service import NvidiaError, generate_book_draft as generate_nvidia_book_draft
from app.validators import (
    validate_account_email,
    validate_name,
    validate_password,
)
from app.services.book_management_service import ensure_book_asset_root, validate_book_payload
from app.services.book_management_service import (
    BookAssetCleanupError,
    delete_book_with_registered_assets,
)

MAX_PHONE_LENGTH = 40
MAX_ADDRESS_LENGTH = 500
MAX_ORGANIZATION_LENGTH = 200


def _validate_new_account_payload(data):
    errors = []
    if not data:
        return ["Request body is required."]

    name, error = validate_name(data.get("name"))
    if error:
        errors.append(error)
    else:
        data["name"] = name

    email, error = validate_account_email(data.get("email"))
    if error:
        errors.append(error)
    else:
        data["email"] = email

    password, error = validate_password(data.get("password"))
    if error:
        errors.append(error)
    else:
        data["password"] = password

    return errors


def _create_account(role):
    data = request.get_json(silent=True)
    errors = _validate_new_account_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400

    email = str(data.get("email")).strip().lower()
    if Parent.query.filter_by(email=email).first():
        return jsonify({"error": "An account with this email already exists."}), 409

    try:
        account = Parent(name=str(data.get("name")).strip(), email=email, role=role)
        account.set_password(str(data.get("password")))
        db.session.add(account)
        db.session.commit()
        return jsonify(
            {"message": f"{role.capitalize()} account created successfully.", "account": account.to_dict()}
        ), 201
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "An account with this email already exists."}), 409
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def register_parent():
    """POST /api/admin/parents — admin creates a parent account directly."""
    return _create_account(ROLE_PARENT)


def register_teacher():
    """POST /api/admin/teachers — create a complete, approved teacher profile."""
    is_multipart = request.mimetype == "multipart/form-data"
    data = request.form.to_dict() if is_multipart else request.get_json(silent=True)
    data = data if isinstance(data, dict) else None
    errors = _validate_new_account_payload(data)

    phone_number = str((data or {}).get("phone_number") or "").strip()
    address = str((data or {}).get("address") or "").strip()
    teacher_type = str((data or {}).get("teacher_type") or "").strip().lower()
    school_name = str((data or {}).get("school_name") or "").strip()
    tuition_name = str((data or {}).get("tuition_name") or "").strip()
    if not phone_number:
        errors.append("phone_number is required.")
    elif len(phone_number) > MAX_PHONE_LENGTH:
        errors.append(f"phone_number must be {MAX_PHONE_LENGTH} characters or fewer.")
    if not address:
        errors.append("address is required.")
    elif len(address) > MAX_ADDRESS_LENGTH:
        errors.append(f"address must be {MAX_ADDRESS_LENGTH} characters or fewer.")
    if teacher_type not in VALID_TEACHER_TYPES:
        errors.append("teacher_type must be school or private_tuition.")
    if len(school_name) > MAX_ORGANIZATION_LENGTH:
        errors.append(f"school_name must be {MAX_ORGANIZATION_LENGTH} characters or fewer.")
    if len(tuition_name) > MAX_ORGANIZATION_LENGTH:
        errors.append(f"tuition_name must be {MAX_ORGANIZATION_LENGTH} characters or fewer.")

    upload = request.files.get("professional_photo") if is_multipart else None
    if upload is None or not upload.filename:
        errors.append("professional_photo is required.")
    else:
        try:
            validate_uploaded_file(upload, "image")
            validate_upload_size(upload, current_app.config["MAX_PROFILE_IMAGE_SIZE_MB"])
            upload.stream.seek(0)
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        oversized = any("exceeds" in error for error in errors)
        invalid_media = any(
            marker in error.lower()
            for error in errors
            for marker in ("unsupported image", "file contents", "mime type")
        )
        return jsonify({"errors": errors}), 413 if oversized else 415 if invalid_media else 400

    email = str(data.get("email")).strip().lower()
    if Parent.query.filter_by(email=email).first():
        return jsonify({"error": "An account with this email already exists."}), 409

    metadata = None
    try:
        account = Parent(
            name=str(data.get("name")).strip(),
            email=email,
            role=ROLE_TEACHER,
            is_banned=False,
        )
        account.set_password(str(data.get("password")))
        db.session.add(account)
        db.session.flush()
        db.session.add(
            TeacherApplication(
                account_id=account.id,
                phone_number=phone_number,
                address=address,
                teacher_type=teacher_type,
                school_name=school_name or None if teacher_type == "school" else None,
                tuition_name=tuition_name or None if teacher_type == "private_tuition" else None,
                approval_status=APPROVAL_APPROVED,
                reviewed_by_id=current_user.id,
                reviewed_at=utc_now(),
            )
        )
        folder = get_user_profile_folder(account.id)
        metadata = upload_asset(
            upload,
            folder,
            resource_type="image",
            public_id=f"{folder}/profile",
            overwrite=False,
            tags=[USER_PROFILE_IMAGE.lower()],
        )
        account.profile_image_url = metadata["secure_url"]
        account.profile_image_public_id = metadata["public_id"]
        db.session.add(
            Asset.from_cloudinary_metadata(
                metadata,
                category=USER_PROFILE_IMAGE,
                owner_user_id=account.id,
                active_slot=f"user:{account.id}:profile",
            )
        )
        db.session.commit()
        return jsonify({
            "message": "Teacher account created successfully.",
            "teacher": _teacher_admin_dict(account),
        }), 201
    except IntegrityError:
        db.session.rollback()
        _cleanup_teacher_creation_upload(metadata)
        return jsonify({"error": "An account with this email already exists."}), 409
    except CloudinaryServiceError:
        db.session.rollback()
        return jsonify({"error": "Professional photo upload failed."}), 503
    except Exception:
        db.session.rollback()
        _cleanup_teacher_creation_upload(metadata)
        current_app.logger.exception(
            "Unexpected failure while an admin created a teacher account"
        )
        return jsonify({"error": "The teacher account could not be created."}), 500


def _cleanup_teacher_creation_upload(metadata):
    if not metadata:
        return
    try:
        delete_asset(
            metadata["public_id"],
            metadata["resource_type"],
            metadata.get("delivery_type") or "upload",
        )
    except CloudinaryServiceError:
        current_app.logger.error(
            "Admin teacher creation upload cleanup failed for asset_id=%s",
            metadata.get("asset_id"),
        )


def create_book():
    """POST /api/admin/books — create a catalog book and its standard games."""
    data = request.get_json(silent=True) or {}
    errors, values = _validate_book_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400

    try:
        book = Book(**values)
        db.session.add(book)
        db.session.flush()
        ensure_book_asset_root(book)
        create_default_mini_games(book)
        db.session.commit()
        return jsonify({
            "message": "Book created with word puzzle, spelling, and quiz games.",
            "book": book.to_dict(include_content=True),
            "mini_games": [game.to_dict(include_content=True) for game in book.mini_games],
        }), 201
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def _validate_book_payload(data):
    return validate_book_payload(data)


def update_book(book_id):
    """PATCH /api/admin/books/<id> — update catalog metadata and media URLs."""
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404

    data = request.get_json(silent=True) or {}
    errors, values = _validate_book_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400

    try:
        for field, value in values.items():
            setattr(book, field, value)
        db.session.commit()
        return jsonify({
            "message": "Book updated successfully.",
            "book": book.to_dict(include_content=True),
        }), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def delete_book(book_id):
    """DELETE /api/admin/books/<id> — remove a book without orphaning sessions."""
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    if ReadingSession.query.filter_by(book_id=book_id).first():
        return jsonify({"error": "This book cannot be deleted because it has reading sessions."}), 409

    try:
        delete_book_with_registered_assets(book)
        return jsonify({"message": "Book deleted successfully."}), 200
    except BookAssetCleanupError:
        return jsonify({"error": "Book asset cleanup is incomplete. Please retry."}), 503
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def generate_book_draft_for_admin():
    """POST /api/admin/book-draft — create an AI draft server-side."""
    data = request.get_json(silent=True) or {}
    age_group = str(data.get("age_group", "")).strip()
    reading_level = str(data.get("reading_level", "")).strip().lower()
    idea = str(data.get("idea", "")).strip()
    model = str(data.get("model") or "").strip()
    errors = []
    if not age_group:
        errors.append("age_group is required.")
    if reading_level not in {"beginner", "intermediate", "advanced"}:
        errors.append("reading_level must be beginner, intermediate, or advanced.")
    if not idea:
        errors.append("idea is required.")
    if len(model) > 200:
        errors.append("model must be 200 characters or fewer.")
    if errors:
        return jsonify({"errors": errors}), 400
    try:
        provider = str(current_app.config.get("BOOK_GENERATION_PROVIDER", "nvidia")).lower()
        if provider == "groq":
            draft = generate_groq_book_draft(
                age_group, reading_level, idea, current_app.config, model=model or None
            )
        elif provider == "nvidia":
            draft = generate_nvidia_book_draft(age_group, reading_level, idea, current_app.config)
        elif provider == "gemini":
            draft = generate_gemini_book_draft(age_group, reading_level, idea, current_app.config)
        else:
            return jsonify({"error": "BOOK_GENERATION_PROVIDER must be groq, nvidia, or gemini."}), 500
        return jsonify({"draft": draft, "provider": provider}), 200
    except (GeminiError, GroqError, NvidiaError) as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception:
        return jsonify({"error": "Book draft generation failed."}), 500


def upload_media():
    """Upload a public book cover or video for use in the catalog."""
    file = request.files.get("file")
    media_type = request.form.get("media_type")
    if not file or not file.filename:
        return jsonify({"errors": ["file is required."]}), 400
    if media_type not in {"image", "video"}:
        return jsonify({"errors": ["media_type must be image or video."]}), 400
    # Videos must be attached to a specific catalog book so their ownership
    # and replacement lifecycle can be tracked by the asset endpoints.
    if media_type == "video":
        return jsonify({"errors": ["Use the book-specific video upload endpoint."]}), 422
    try:
        validate_upload_size(
            file,
            current_app.config["MAX_PROFILE_IMAGE_SIZE_MB"],
        )
        url = upload_book_media(file, media_type, current_user.id, current_app.config)
        return jsonify({"url": url}), 201
    except ValueError as exc:
        message = str(exc)
        status = 413 if "exceeds" in message else 415
        return jsonify({"errors": [message]}), status
    except CloudinaryServiceError:
        return jsonify({"error": "Media upload failed."}), 503
    except Exception:
        return jsonify({"error": "Media upload failed."}), 500


def _list_accounts_by_role(role):
    accounts = Parent.query.filter_by(role=role).order_by(Parent.id.desc()).all()
    results = []
    for account in accounts:
        item = account.to_dict()
        if role == ROLE_PARENT:
            item["children_count"] = Child.query.filter_by(parent_id=account.id).count()
        results.append(item)
    return results


def list_parents():
    """GET /api/admin/parents"""
    return jsonify({"parents": _list_accounts_by_role(ROLE_PARENT)}), 200


def _teacher_admin_dict(account):
    data = account.to_dict()
    profile = account.teacher_application
    if profile:
        data.update(profile.to_private_dict())
    else:
        data.update(
            approval_status=APPROVAL_APPROVED,
            phone_number=None,
            address=None,
            teacher_type=None,
            school_name=None,
            tuition_name=None,
            reviewed_by_id=None,
            reviewed_at=None,
            rejection_reason=None,
        )
    return data


def list_teachers():
    """GET /api/admin/teachers?status=... — private application details."""
    status = str(request.args.get("status") or "").strip().lower()
    if status and status not in VALID_APPROVAL_STATUSES:
        return jsonify({"error": "status must be pending, approved, or rejected."}), 400
    query = Parent.query.filter_by(role=ROLE_TEACHER)
    if status:
        query = query.join(
            TeacherApplication,
            TeacherApplication.account_id == Parent.id,
        ).filter(
            TeacherApplication.approval_status == status
        )
    accounts = query.order_by(Parent.id.desc()).all()
    return jsonify({"teachers": [_teacher_admin_dict(item) for item in accounts]}), 200


def get_teacher(teacher_id):
    account = db.session.get(Parent, teacher_id)
    if not account or account.role != ROLE_TEACHER:
        return jsonify({"error": "Teacher not found."}), 404
    return jsonify({"teacher": _teacher_admin_dict(account)}), 200


def _review_teacher(teacher_id, approval_status):
    account = db.session.get(Parent, teacher_id)
    if not account or account.role != ROLE_TEACHER:
        return jsonify({"error": "Teacher not found."}), 404
    profile = account.teacher_application
    created_profile = profile is None
    if profile is None:
        profile = TeacherApplication(
            account_id=account.id,
            approval_status=APPROVAL_APPROVED,
        )
        db.session.add(profile)

    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "").strip()
    if len(reason) > 1000:
        return jsonify({"error": "reason must be 1000 characters or fewer."}), 400

    if not created_profile and profile.approval_status == approval_status:
        return jsonify({
            "message": f"Teacher is already {approval_status}.",
            "teacher": _teacher_admin_dict(account),
        }), 200

    try:
        profile.approval_status = approval_status
        profile.reviewed_by_id = current_user.id
        profile.reviewed_at = utc_now()
        profile.rejection_reason = reason or None if approval_status == APPROVAL_REJECTED else None
        db.session.commit()
        return jsonify({
            "message": f"Teacher {approval_status} successfully.",
            "teacher": _teacher_admin_dict(account),
        }), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Unexpected failure while reviewing a teacher application"
        )
        return jsonify({"error": "The teacher application could not be updated."}), 500


def approve_teacher(teacher_id):
    return _review_teacher(teacher_id, APPROVAL_APPROVED)


def reject_teacher(teacher_id):
    return _review_teacher(teacher_id, APPROVAL_REJECTED)


def book_analytics():
    """Return per-book aggregate engagement without child-level data."""
    search = str(request.args.get("search") or "").strip()
    raw_creator_id = str(request.args.get("creator_id") or "").strip()
    sort = str(request.args.get("sort") or "views").strip().lower()
    if sort not in {"views", "reads", "likes"}:
        return jsonify({"error": "sort must be views, reads, or likes."}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    except (TypeError, ValueError):
        return jsonify({"error": "page and per_page must be positive integers."}), 400

    views = db.session.query(
        BookView.book_id.label("book_id"),
        func.count(BookView.id).label("total_views"),
        func.count(func.distinct(BookView.account_id)).label("unique_viewers"),
    ).group_by(BookView.book_id).subquery()
    reads = db.session.query(
        ReadingSession.book_id.label("book_id"),
        func.count(ReadingSession.id).label("total_reads"),
        func.sum(case((ReadingSession.completed_at.isnot(None), 1), else_=0)).label("completed_reads"),
        func.count(func.distinct(ReadingSession.child_id)).label("unique_readers"),
    ).group_by(ReadingSession.book_id).subquery()
    likes = db.session.query(
        BookLike.book_id.label("book_id"),
        func.count(BookLike.id).label("likes"),
    ).group_by(BookLike.book_id).subquery()

    query = db.session.query(
        Book,
        func.coalesce(views.c.total_views, 0).label("total_views"),
        func.coalesce(views.c.unique_viewers, 0).label("unique_viewers"),
        func.coalesce(reads.c.total_reads, 0).label("total_reads"),
        func.coalesce(reads.c.completed_reads, 0).label("completed_reads"),
        func.coalesce(reads.c.unique_readers, 0).label("unique_readers"),
        func.coalesce(likes.c.likes, 0).label("likes"),
    ).outerjoin(views, views.c.book_id == Book.id).outerjoin(
        reads, reads.c.book_id == Book.id
    ).outerjoin(likes, likes.c.book_id == Book.id).outerjoin(
        Parent, Book.created_by_account_id == Parent.id
    )
    if search:
        query = query.filter(or_(
            Book.title.ilike(f"%{search}%"),
            Parent.name.ilike(f"%{search}%"),
            Book.creator_name_snapshot.ilike(f"%{search}%"),
        ))
    if raw_creator_id:
        try:
            creator_id = int(raw_creator_id)
        except ValueError:
            return jsonify({"error": "creator_id must be a positive integer."}), 400
        if creator_id <= 0:
            return jsonify({"error": "creator_id must be a positive integer."}), 400
        query = query.filter(Book.created_by_account_id == creator_id)
    sort_column = {
        "views": func.coalesce(views.c.total_views, 0),
        "reads": func.coalesce(reads.c.total_reads, 0),
        "likes": func.coalesce(likes.c.likes, 0),
    }[sort]
    total = query.count()
    rows = query.order_by(sort_column.desc(), Book.title.asc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()
    return jsonify({
        "books": [
            {
                **book.to_dict(),
                "book_id": book.id,
                "total_views": int(total_views or 0),
                "unique_viewers": int(unique_viewers or 0),
                "total_reads": int(total_reads or 0),
                "completed_reads": int(completed_reads or 0),
                "unique_readers": int(unique_readers or 0),
                "likes": int(like_count or 0),
            }
            for book, total_views, unique_viewers, total_reads, completed_reads, unique_readers, like_count in rows
        ],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    }), 200


def get_parent(parent_id):
    """GET /api/admin/parents/<id> — full detail including their children."""
    parent = db.session.get(Parent, parent_id)
    if not parent or parent.role != ROLE_PARENT:
        return jsonify({"error": "Parent not found."}), 404

    children = Child.query.filter_by(parent_id=parent.id).order_by(Child.id.desc()).all()
    data = parent.to_dict()
    data["children"] = [c.to_dict() for c in children]
    return jsonify({"parent": data}), 200


def _get_target_account(account_id, expected_role=None):
    account = db.session.get(Parent, account_id)
    if not account:
        return None, (jsonify({"error": "Account not found."}), 404)
    if expected_role and account.role != expected_role:
        return None, (jsonify({"error": "Account not found."}), 404)
    if account.id == current_user.id:
        return None, (jsonify({"error": "You cannot perform this action on your own account."}), 400)
    if account.is_admin:
        return None, (jsonify({"error": "Admin accounts cannot be managed through this endpoint."}), 403)
    return account, None


def ban_account(account_id, expected_role=None):
    """PATCH /api/admin/parents/<id>/ban or /api/admin/teachers/<id>/ban"""
    account, error_response = _get_target_account(account_id, expected_role)
    if error_response:
        return error_response

    try:
        account.is_banned = True
        db.session.commit()
        return jsonify({"message": "Account banned successfully.", "account": account.to_dict()}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def unban_account(account_id, expected_role=None):
    """PATCH /api/admin/parents/<id>/unban or /api/admin/teachers/<id>/unban"""
    account, error_response = _get_target_account(account_id, expected_role)
    if error_response:
        return error_response

    try:
        account.is_banned = False
        db.session.commit()
        return jsonify({"message": "Account unbanned successfully.", "account": account.to_dict()}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def delete_account(account_id, expected_role=None):
    """DELETE /api/admin/parents/<id> or /api/admin/teachers/<id>

    Deleting a parent cascades to their children and voice profiles, same as
    a parent deleting their own account.
    """
    account, error_response = _get_target_account(account_id, expected_role)
    if error_response:
        return error_response

    try:
        asset_refs = collect_account_asset_refs(account)
        remove_account_asset_ledger_rows(account.id)
        db.session.delete(account)
        db.session.commit()
        schedule_account_asset_cleanup(asset_refs)
        return jsonify({"message": "Account deleted successfully. External asset cleanup is in progress."}), 202
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500
