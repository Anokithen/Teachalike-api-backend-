from flask import current_app, jsonify, request
from datetime import datetime, timezone
from sqlalchemy import or_

from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from app.extensions import db
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.models.asset_model import Asset, USER_PROFILE_IMAGE
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER
from app.models.auth_identity_model import AccountIdentity
from app.models.email_delivery_model import EmailDelivery
from app.models.email_verification_token_model import PURPOSE_EMAIL_VERIFICATION, EmailVerificationToken
from app.models.revoked_token_model import RevokedToken
from app.models.teacher_application_model import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TeacherApplication,
    VALID_TEACHER_TYPES,
)
from app.security import (
    anonymized_key,
    google_login_attempts,
    login_attempts,
    registration_attempts,
    resend_verification_attempts,
    verify_email_attempts,
)
from app.services.auth_email_service import (
    create_verification_token_and_event,
    hash_token,
    mask_email,
    verification_url,
)
from app.services.email_service import send_delivery
from app.validators import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    validate_account_email,
    validate_name,
    validate_password,
)
from app.services.cloudinary_path_service import get_user_profile_folder
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    upload_asset,
    validate_upload_size,
    validate_uploaded_file,
)
from app.utils import utc_now

MAX_PHONE_LENGTH = 40
MAX_ADDRESS_LENGTH = 500
MAX_ORGANIZATION_LENGTH = 200
GENERIC_RESEND_MESSAGE = "If this account requires verification, a new email will be sent."


def _code_payload(error, code, **extra):
    return {"error": error, "code": code, "error_code": code, **extra}


def _issue_login_response(account, message="Login successful."):
    account.last_login_at = utc_now()
    db.session.commit()
    access_token = create_access_token(identity=account)
    refresh_token = create_refresh_token(identity=account)
    return jsonify(
        {
            "message": message,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "parent": account.to_self_dict(),
        }
    ), 200


def _teacher_access_error(account):
    """Return the stable approval error for a blocked teacher, if any."""
    if not account or not account.is_teacher or not account.teacher_application:
        return None
    profile = account.teacher_application
    if profile.approval_status == APPROVAL_REJECTED:
        payload = {
            "error": "Your teacher registration was rejected by an administrator.",
            "error_code": "TEACHER_APPROVAL_REJECTED",
            "code": "TEACHER_APPROVAL_REJECTED",
        }
        if profile.rejection_reason:
            payload["rejection_reason"] = profile.rejection_reason
        return payload
    if profile.approval_status != APPROVAL_APPROVED:
        return {
            "error": "Your teacher account is waiting for administrator approval.",
            "error_code": "TEACHER_APPROVAL_PENDING",
            "code": "TEACHER_APPROVAL_PENDING",
        }
    return None

def _validate_register_payload(data, account_type):
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

    if account_type != ROLE_PARENT:
        errors.append("Public registration only creates parent accounts.")

    if account_type == ROLE_TEACHER:
        phone_number = str(data.get("phone_number") or "").strip()
        address = str(data.get("address") or "").strip()
        teacher_type = str(data.get("teacher_type") or "").strip().lower()
        school_name = str(data.get("school_name") or "").strip()
        tuition_name = str(data.get("tuition_name") or "").strip()
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
            errors.append(
                f"school_name must be {MAX_ORGANIZATION_LENGTH} characters or fewer."
            )
        if len(tuition_name) > MAX_ORGANIZATION_LENGTH:
            errors.append(
                f"tuition_name must be {MAX_ORGANIZATION_LENGTH} characters or fewer."
            )
        data.update(
            phone_number=phone_number,
            address=address,
            teacher_type=teacher_type,
            school_name=school_name or None,
            tuition_name=tuition_name or None,
        )

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

    is_multipart = request.mimetype == "multipart/form-data"
    data = request.form.to_dict() if is_multipart else request.get_json(silent=True)
    data = data if isinstance(data, dict) else None
    requested_account_type = str((data or {}).get("account_type") or ROLE_PARENT).strip().lower()
    if requested_account_type != ROLE_PARENT:
        return jsonify({"errors": ["Public registration only creates parent accounts."]}), 400
    account_type = ROLE_PARENT
    errors = _validate_register_payload(data, account_type)

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

    try:
        account = Parent(
            name=str(data.get("name")).strip(),
            email=email,
            role=account_type,
            is_banned=False,
            email_verified=False,
            auth_provider="password",
        )
        account.set_password(str(data.get("password")))
        db.session.add(account)
        db.session.flush()
        raw_token, delivery = create_verification_token_and_event(account, request)
        db.session.commit()
        try:
            send_delivery(delivery, verification_url=verification_url(raw_token))
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Verification email post-commit send failed.")
        return jsonify({
            "message": "Account created. Please check your email to verify your account.",
            "requires_email_verification": True,
            "email": mask_email(account.email),
            "parent": account.to_dict(),
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "An account with this email already exists."}), 409
    except CloudinaryServiceError:
        db.session.rollback()
        return jsonify({"error": "Professional photo upload failed."}), 503
    except SQLAlchemyError:
        db.session.rollback()
        if account_type == ROLE_TEACHER and "metadata" in locals():
            _cleanup_registration_upload(metadata)
        current_app.logger.exception(
            "Database failure while saving a public teacher application"
            if account_type == ROLE_TEACHER
            else "Database failure while creating a parent account"
        )
        message = (
            "Teacher registration metadata could not be saved."
            if account_type == ROLE_TEACHER
            else "An internal server error occurred."
        )
        return jsonify({"error": message}), 500
    except Exception:
        db.session.rollback()
        if account_type == ROLE_TEACHER and "metadata" in locals():
            _cleanup_registration_upload(metadata)
        current_app.logger.exception(
            "Unexpected failure while saving a public teacher application"
            if account_type == ROLE_TEACHER
            else "Unexpected failure while creating a parent account"
        )
        return jsonify({"error": "An internal server error occurred."}), 500


def _cleanup_registration_upload(metadata):
    try:
        delete_asset(
            metadata["public_id"],
            metadata["resource_type"],
            metadata.get("delivery_type") or "upload",
        )
    except CloudinaryServiceError:
        current_app.logger.error(
            "Teacher registration upload cleanup failed for asset_id=%s",
            metadata.get("asset_id"),
        )


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
            return jsonify(_code_payload("This account has been banned. Contact an administrator.", "ACCOUNT_BANNED")), 403
        if not parent.email_verified:
            return jsonify(_code_payload(
                "Please verify your email before signing in.",
                "EMAIL_NOT_VERIFIED",
                can_resend_verification=True,
            )), 403

        approval_error = _teacher_access_error(parent)
        if approval_error:
            return jsonify(approval_error), 403

        login_attempts.reset(account_key)
        return _issue_login_response(parent)
    except Exception:
        return jsonify({"error": "An internal server error occurred."}), 500


@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    parent = db.session.get(Parent, int(identity))
    if not parent:
        return jsonify({"error": "Account not found."}), 404
    if parent.is_banned:
        return jsonify(_code_payload("This account has been banned. Contact an administrator.", "ACCOUNT_BANNED")), 403
    if not parent.email_verified:
        return jsonify(_code_payload("Please verify your email before signing in.", "EMAIL_NOT_VERIFIED")), 403
    approval_error = _teacher_access_error(parent)
    if approval_error:
        return jsonify(approval_error), 403
    access_token = create_access_token(identity=parent)
    return jsonify({"access_token": access_token}), 200


def verify_email():
    key = anonymized_key("verify-email-ip", request.remote_addr or "unknown")
    limit = current_app.config["VERIFY_EMAIL_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["VERIFY_EMAIL_RATE_LIMIT_WINDOW_SECONDS"]
    blocked, retry_after = verify_email_attempts.blocked(key, limit, window)
    if blocked:
        response = jsonify(_code_payload("Too many verification attempts. Please try again later.", "RATE_LIMITED"))
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response
    verify_email_attempts.record_failure(key, window)

    data = request.get_json(silent=True) or {}
    raw_token = str(data.get("token") or "").strip()
    if not raw_token:
        return jsonify(_code_payload("Verification token is required.", "TOKEN_REQUIRED")), 400

    now = utc_now()
    try:
        token = EmailVerificationToken.query.filter_by(
            token_hash=hash_token(raw_token),
            purpose=PURPOSE_EMAIL_VERIFICATION,
        ).with_for_update().first()
        expires_at = token.expires_at if token else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            not token
            or token.used_at is not None
            or token.revoked_at is not None
            or expires_at < now
        ):
            db.session.rollback()
            return jsonify(_code_payload("This verification link is invalid or expired.", "INVALID_OR_EXPIRED_TOKEN")), 400
        account = db.session.get(Parent, token.account_id)
        if not account:
            db.session.rollback()
            return jsonify(_code_payload("This verification link is invalid or expired.", "INVALID_OR_EXPIRED_TOKEN")), 400
        token.used_at = now
        token.revoked_at = now
        account.email_verified = True
        account.email_verified_at = now
        db.session.commit()
        verify_email_attempts.reset(key)
        return jsonify({"message": "Email verified! You can now sign in to TeachAlike."}), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Email verification failed.")
        return jsonify({"error": "An internal server error occurred."}), 500


def resend_verification():
    data = request.get_json(silent=True) or {}
    email, _error = validate_account_email(data.get("email"), required=False)
    normalized_email = str(email or "").strip().lower()
    ip_key = anonymized_key("resend-ip", request.remote_addr or "unknown")
    email_key = anonymized_key("resend-email", normalized_email or "missing")
    limit = current_app.config["RESEND_VERIFICATION_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["RESEND_VERIFICATION_RATE_LIMIT_WINDOW_SECONDS"]
    for key in (ip_key, email_key):
        blocked, retry_after = resend_verification_attempts.blocked(key, limit, window)
        if blocked:
            response = jsonify({"message": GENERIC_RESEND_MESSAGE})
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
    resend_verification_attempts.record_failure(ip_key, window)
    resend_verification_attempts.record_failure(email_key, window)

    account = Parent.query.filter_by(email=normalized_email).first() if normalized_email else None
    if not account or account.email_verified:
        return jsonify({"message": GENERIC_RESEND_MESSAGE}), 200
    recent = EmailVerificationToken.query.filter(
        EmailVerificationToken.account_id == account.id,
        EmailVerificationToken.purpose == PURPOSE_EMAIL_VERIFICATION,
        EmailVerificationToken.used_at.is_(None),
        EmailVerificationToken.revoked_at.is_(None),
        EmailVerificationToken.created_at > utc_now() - current_app.config["MAIL_RETRY_DELTA_FACTORY"](current_app.config["RESEND_VERIFICATION_COOLDOWN_SECONDS"]),
    ).first()
    if recent:
        return jsonify({"message": GENERIC_RESEND_MESSAGE}), 200
    try:
        raw_token, delivery = create_verification_token_and_event(account, request)
        db.session.commit()
        send_delivery(delivery, verification_url=verification_url(raw_token))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Resend verification failed safely.")
    return jsonify({"message": GENERIC_RESEND_MESSAGE}), 200


def _verify_google_credential(credential):
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except Exception as exc:
        raise ValueError("GOOGLE_AUTH_LIBRARY_UNAVAILABLE") from exc
    client_id = current_app.config["GOOGLE_AUTH_CLIENT_ID"]
    if not client_id:
        raise ValueError("GOOGLE_AUTH_NOT_CONFIGURED")
    claims = id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    issuer = claims.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("INVALID_GOOGLE_ISSUER")
    if not claims.get("sub"):
        raise ValueError("GOOGLE_SUBJECT_REQUIRED")
    if not claims.get("email_verified"):
        raise ValueError("GOOGLE_EMAIL_NOT_VERIFIED")
    email, error = validate_account_email(claims.get("email"))
    if error:
        raise ValueError("GOOGLE_EMAIL_INVALID")
    claims["email"] = email
    return claims


def google_auth():
    key = anonymized_key("google-login-ip", request.remote_addr or "unknown")
    limit = current_app.config["GOOGLE_LOGIN_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["GOOGLE_LOGIN_RATE_LIMIT_WINDOW_SECONDS"]
    blocked, retry_after = google_login_attempts.blocked(key, limit, window)
    if blocked:
        response = jsonify(_code_payload("Too many Google sign-in attempts. Please try again later.", "RATE_LIMITED"))
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response
    google_login_attempts.record_failure(key, window)

    data = request.get_json(silent=True) or {}
    credential = str(data.get("credential") or "").strip()
    if not credential:
        return jsonify(_code_payload("Google credential is required.", "GOOGLE_CREDENTIAL_REQUIRED")), 400
    try:
        claims = _verify_google_credential(credential)
    except ValueError as exc:
        code = str(exc)
        return jsonify(_code_payload("Google sign-in could not be verified.", code)), 401

    subject = str(claims["sub"])
    email = claims["email"]
    name = str(claims.get("name") or email.split("@", 1)[0]).strip()[:120]
    now = utc_now()
    try:
        identity = AccountIdentity.query.filter_by(provider="google", provider_subject=subject).first()
        account = identity.account if identity else None
        if not account:
            account = Parent.query.filter_by(email=email).first()
            if not account:
                account = Parent(
                    name=name or "TeachAlike Parent",
                    email=email,
                    password="",
                    role=ROLE_PARENT,
                    is_banned=False,
                    email_verified=True,
                    email_verified_at=now,
                    auth_provider="google",
                    google_subject=subject,
                )
                db.session.add(account)
                db.session.flush()
            elif account.role == ROLE_ADMIN:
                return jsonify(_code_payload("Administrators must link Google from an authenticated account before Google login is allowed.", "ADMIN_GOOGLE_LINK_REQUIRED")), 403
            else:
                account.email_verified = True
                account.email_verified_at = account.email_verified_at or now
                if not account.google_subject:
                    account.google_subject = subject
            if not AccountIdentity.query.filter_by(provider="google", provider_subject=subject).first():
                db.session.add(AccountIdentity(
                    account_id=account.id,
                    provider="google",
                    provider_subject=subject,
                    provider_email=email,
                ))
        if account.is_banned:
            db.session.rollback()
            return jsonify(_code_payload("This account has been banned. Contact an administrator.", "ACCOUNT_BANNED")), 403
        approval_error = _teacher_access_error(account)
        if approval_error:
            db.session.rollback()
            return jsonify(approval_error), 403
        google_login_attempts.reset(key)
        return _issue_login_response(account, "Google login successful.")
    except IntegrityError:
        db.session.rollback()
        return jsonify(_code_payload("A TeachAlike account already exists for this Google identity.", "GOOGLE_ACCOUNT_CONFLICT")), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Google authentication failed.")
        return jsonify({"error": "An internal server error occurred."}), 500


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
