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
