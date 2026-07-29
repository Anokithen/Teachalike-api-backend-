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


def register():
    registration_key = anonymized_key(
        "register-ip", request.remote_addr or "unknown"
    )
    limit = current_app.config["REGISTER_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["REGISTER_RATE_LIMIT_WINDOW_SECONDS"]
    blocked, retry_after = registration_attempts.blocked(
        registration_key, limit, window
    )
    if blocked:
        response = jsonify(
            {"error": "Too many registration attempts. Please try again later."}
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response
    registration_attempts.record_failure(registration_key, window)

    data = request.get_json(silent=True)
    errors = _validate_register_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400

    email = str(data.get("email")).strip().lower()

    if Parent.query.filter_by(email=email).first():
        return jsonify({"error": "An account with this email already exists."}), 409

    try:
        # Public registration must never be able to create privileged or
        # staff accounts. Teachers and admins are created through the
        # authenticated admin endpoints.
        parent = Parent(
            name=str(data.get("name")).strip(),
            email=email,
            role="parent",
        )

        parent.set_password(str(data.get("password")))

        db.session.add(parent)
        db.session.commit()

        return jsonify({
            "message": "Parent account created successfully.",
            "parent": parent.to_dict()
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "An account with this email already exists."}), 409
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def login():
    data = request.get_json(silent=True)
    errors = _validate_login_payload(data)
    if errors:
        return jsonify({"errors": errors}), 400

    email = str(data.get("email")).strip().lower()
    ip_key = anonymized_key("login-ip", request.remote_addr or "unknown")
    account_key = anonymized_key("login-account", email)
    limit = current_app.config["LOGIN_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"]
    for key in (ip_key, account_key):
        blocked, retry_after = login_attempts.blocked(key, limit, window)
        if blocked:
            response = jsonify(
                {"error": "Too many failed login attempts. Please try again later."}
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response

    try:
        parent = Parent.query.filter_by(email=email).first()

        if not parent or not parent.check_password(str(data.get("password"))):
            login_attempts.record_failure(ip_key, window)
            login_attempts.record_failure(account_key, window)
            return jsonify({"error": "Invalid email or password."}), 401

        if parent.is_banned:
            return jsonify({"error": "This account has been banned. Contact an administrator."}), 403

        login_attempts.reset(account_key)
        access_token = create_access_token(identity=parent)
        refresh_token = create_refresh_token(identity=parent)
        return jsonify(
            {
                "message": "Login successful.",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "parent": parent.to_dict(),
            }
        ), 200
    except Exception:
        return jsonify({"error": "An internal server error occurred."}), 500


@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    parent = db.session.get(Parent, int(identity))
    if not parent:
        return jsonify({"error": "Account not found."}), 404
    if parent.is_banned:
        return jsonify({"error": "This account has been banned. Contact an administrator."}), 403
    access_token = create_access_token(identity=parent)
    return jsonify({"access_token": access_token}), 200


@jwt_required()
def logout():
    access_payload = get_jwt()
    payloads = [access_payload]
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token")
    if refresh_token:
        try:
            refresh_payload = decode_token(str(refresh_token))
        except Exception:
            refresh_payload = None
        if refresh_payload and (
            refresh_payload.get("type") != "refresh"
            or str(refresh_payload.get("sub")) != str(get_jwt_identity())
        ):
            refresh_payload = None
        if refresh_payload:
            payloads.append(refresh_payload)

    try:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        RevokedToken.query.filter(RevokedToken.expires_at < now_naive).delete(
            synchronize_session=False
        )
        existing = {
            row.jti
            for row in RevokedToken.query.filter(
                RevokedToken.jti.in_([payload["jti"] for payload in payloads])
            ).all()
        }
        for payload in payloads:
            if payload["jti"] in existing:
                continue
            db.session.add(
                RevokedToken(
                    jti=payload["jti"],
                    token_type=payload["type"],
                    expires_at=datetime.fromtimestamp(
                        payload["exp"], timezone.utc
                    ).replace(tzinfo=None),
                )
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Logout could not be completed."}), 500
    return jsonify({"message": "Logged out successfully."}), 200
