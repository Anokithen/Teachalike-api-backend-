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
