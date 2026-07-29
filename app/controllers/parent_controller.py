from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from app.extensions import db
from sqlalchemy.exc import IntegrityError
from app.models.parent_model import Parent
from app.security import account_password_attempts
from app.services.account_cleanup_service import collect_account_asset_refs, schedule_account_asset_cleanup
from app.validators import (
    MAX_PASSWORD_LENGTH,
    validate_account_email,
    validate_name,
    validate_password,
)


def _verify_account_password(data, action):
    current_password = str(data.get("current_password", ""))
    if not current_password:
        return None, jsonify(
            {"errors": ["current_password is required."]}
        ), 400
    if len(current_password) > MAX_PASSWORD_LENGTH:
        return None, jsonify(
            {
                "errors": [
                    f"current_password must be {MAX_PASSWORD_LENGTH} characters or fewer."
                ]
            }
        ), 400

    key = f"account-password:{action}:{current_user.id}"
    limit = current_app.config["ACCOUNT_PASSWORD_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["ACCOUNT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS"]
    blocked, retry_after = account_password_attempts.blocked(
        key, limit, window
    )
    if blocked:
        response = jsonify(
            {"error": "Too many incorrect password attempts. Please try again later."}
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return None, response, None
    if not current_user.check_password(current_password):
        account_password_attempts.record_failure(key, window)
        return None, jsonify(
            {"error": "The current account password is incorrect."}
        ), 401
    return key, None, None


def get_me():
    return jsonify({"parent": current_user.to_dict()}), 200


def update_me():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required."}), 400

    errors = []
    parent = current_user

    if "name" in data:
        name, error = validate_name(data.get("name"))
        if error:
            errors.append(error.replace("is required", "cannot be empty"))
        else:
            data["name"] = name

    if "email" in data:
        email, error = validate_account_email(data.get("email"))
        if error:
            errors.append(error.replace("is required", "cannot be empty"))
        else:
            data["email"] = email
            existing = Parent.query.filter_by(email=email).first()
            if existing and existing.id != parent.id:
                errors.append("An account with this email already exists.")

    if "password" in data:
        password, error = validate_password(data.get("password"))
        if error:
            errors.append(error)
        else:
            data["password"] = password

    sensitive_change = "password" in data or (
        "email" in data and data.get("email") != parent.email
    )
    password_attempt_key = None
    if not errors and sensitive_change:
        password_attempt_key, error_response, status = (
            _verify_account_password(data, "profile-update")
        )
        if error_response:
            return (
                error_response
                if status is None
                else (error_response, status)
            )

    if errors:
        return jsonify({"errors": errors}), 400

    try:
        if "name" in data:
            parent.name = str(data.get("name")).strip()
        if "email" in data:
            parent.email = data.get("email")
        if "password" in data:
            parent.set_password(str(data.get("password")))

        db.session.commit()
        if password_attempt_key:
            account_password_attempts.reset(password_attempt_key)
        return jsonify({"message": "Profile updated successfully.", "parent": parent.to_dict()}), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "An account with this email already exists."}), 409
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def upload_profile_image_for_current_user():
    from app.controllers.asset_controller import upload_user_profile_image

    return upload_user_profile_image(legacy_response=True)


def delete_profile_image_for_current_user():
    from app.controllers.asset_controller import delete_user_profile_image_legacy

    return delete_user_profile_image_legacy()


def delete_me():
    data = request.get_json(silent=True) or {}
    password_attempt_key, error_response, status = _verify_account_password(
        data, "account-delete"
    )
    if error_response:
        return (
            error_response
            if status is None
            else (error_response, status)
        )

    parent = current_user
    try:
        asset_refs = collect_account_asset_refs(parent)
        db.session.delete(parent)  # cascades to children & voice_profiles
        db.session.commit()
        account_password_attempts.reset(password_attempt_key)
        schedule_account_asset_cleanup(asset_refs)
        return jsonify({"message": "Account deleted successfully. External asset cleanup is in progress."}), 202
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500
