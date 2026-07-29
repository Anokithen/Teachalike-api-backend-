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
