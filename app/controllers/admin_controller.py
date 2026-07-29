from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from app.extensions import db
from sqlalchemy.exc import IntegrityError
from app.models.parent_model import Parent, ROLE_PARENT, ROLE_TEACHER, ROLE_ADMIN, VALID_ROLES
from app.models.child_model import Child
from app.models.book_model import Book
from app.models.reading_session_model import ReadingSession
from app.services.book_games import create_default_mini_games
from app.services.account_cleanup_service import collect_account_asset_refs, schedule_account_asset_cleanup
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    upload_book_media,
    validate_upload_size,
)
from app.services.gemini_service import GeminiError, generate_book_draft as generate_gemini_book_draft
from app.services.groq_service import GroqError, generate_book_draft as generate_groq_book_draft
from app.services.nvidia_service import NvidiaError, generate_book_draft as generate_nvidia_book_draft
from app.validators import (
    MAX_URL_LENGTH,
    is_safe_http_url,
    validate_account_email,
    validate_name,
    validate_password,
)


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
    """POST /api/admin/teachers — admin creates a teacher account."""
    return _create_account(ROLE_TEACHER)


def register_admin():
    """POST /api/admin/admins — an existing admin creates another admin account."""
    return _create_account(ROLE_ADMIN)


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
    """Validate the shared fields used when creating or editing a book."""
    errors = []
    title = str(data.get("title", "")).strip()
    age_group = str(data.get("age_group", "")).strip()
    reading_level = str(data.get("reading_level", "")).strip().lower()
    image_urls = data.get("image_urls") or []

    if not title:
        errors.append("title is required.")
    elif len(title) > 200:
        errors.append("title must be 200 characters or fewer.")
    if not age_group:
        errors.append("age_group is required.")
    elif len(age_group) > 50:
        errors.append("age_group must be 50 characters or fewer.")
    if reading_level not in {"beginner", "intermediate", "advanced"}:
        errors.append("reading_level must be beginner, intermediate, or advanced.")
    if not isinstance(image_urls, list) or len(image_urls) > 8 or any(
        not isinstance(url, str) or not is_safe_http_url(url)
        for url in image_urls
    ):
        errors.append(
            f"image_urls must contain up to 8 valid HTTPS URLs (or local HTTP URLs) of "
            f"{MAX_URL_LENGTH} characters or fewer."
        )

    url_fields = {
        "content_url": str(data.get("content_url", "")).strip(),
        "cover_image_url": str(data.get("cover_image_url", "")).strip(),
        "video_url": str(data.get("video_url", "")).strip(),
    }
    for field_name, value in url_fields.items():
        if value and not is_safe_http_url(value):
            errors.append(
                f"{field_name} must be a valid HTTPS URL (or local HTTP URL) of "
                f"{MAX_URL_LENGTH} characters or fewer."
            )

    return errors, {
        "title": title,
        "age_group": age_group,
        "reading_level": reading_level,
        "text_content": str(data.get("text_content", "")).strip() or None,
        "content_url": url_fields["content_url"] or None,
        "cover_image_url": url_fields["cover_image_url"] or None,
        "video_url": url_fields["video_url"] or None,
        "image_urls": [url.strip() for url in image_urls] if isinstance(image_urls, list) else [],
    }


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
        db.session.delete(book)
        db.session.commit()
        return jsonify({"message": "Book deleted successfully."}), 200
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


def list_teachers():
    """GET /api/admin/teachers"""
    return jsonify({"teachers": _list_accounts_by_role(ROLE_TEACHER)}), 200


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
        db.session.delete(account)
        db.session.commit()
        schedule_account_asset_cleanup(asset_refs)
        return jsonify({"message": "Account deleted successfully. External asset cleanup is in progress."}), 202
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500
