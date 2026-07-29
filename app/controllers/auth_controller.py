from flask import current_app, jsonify, request
from datetime import datetime, timezone

from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from app.extensions import db
from sqlalchemy.exc import IntegrityError
from app.models.parent_model import Parent
from app.models.revoked_token_model import RevokedToken
from app.security import (
    anonymized_key,
    login_attempts,
    registration_attempts,
)
from app.validators import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    validate_account_email,
    validate_name,
    validate_password,
)

def _validate_register_payload(data):
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


def _validate_login_payload(data):
    errors = []

    if not data:
        return ["Request body is required."]

    if not data.get("email"):
        errors.append("email is required.")
    elif len(str(data.get("email"))) > MAX_EMAIL_LENGTH:
        errors.append(f"email must be {MAX_EMAIL_LENGTH} characters or fewer.")
    if not data.get("password"):
        errors.append("password is required.")
    elif len(str(data.get("password"))) > MAX_PASSWORD_LENGTH:
        errors.append(f"password must be {MAX_PASSWORD_LENGTH} characters or fewer.")

    return errors
