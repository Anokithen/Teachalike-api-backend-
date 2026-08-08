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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.models.asset_model import Asset, USER_PROFILE_IMAGE
from app.models.parent_model import Parent, ROLE_PARENT, ROLE_TEACHER
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
from app.services.cloudinary_path_service import get_user_profile_folder
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    upload_asset,
    validate_upload_size,
    validate_uploaded_file,
)

MAX_PHONE_LENGTH = 40
MAX_ADDRESS_LENGTH = 500
MAX_ORGANIZATION_LENGTH = 200
def _teacher_access_error(account):
    """Return the stable approval error for a blocked teacher, if any."""
    if not account or not account.is_teacher or not account.teacher_application:
        return None
    profile = account.teacher_application
    if profile.approval_status == APPROVAL_REJECTED:
        payload = {
            "error": "Your teacher registration was rejected by an administrator.",
            "error_code": "TEACHER_APPROVAL_REJECTED",
        }
        if profile.rejection_reason:
            payload["rejection_reason"] = profile.rejection_reason
        return payload
    if profile.approval_status != APPROVAL_APPROVED:
        return {
            "error": "Your teacher account is waiting for administrator approval.",
            "error_code": "TEACHER_APPROVAL_PENDING",
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

    if account_type not in {ROLE_PARENT, ROLE_TEACHER}:
        errors.append("account_type must be parent or teacher.")

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
    account_type = str((data or {}).get("account_type") or ROLE_PARENT).strip().lower()
    errors = _validate_register_payload(data, account_type)

    upload = None
    if account_type == ROLE_TEACHER:
        upload = request.files.get("professional_photo") if is_multipart else None
        if upload is None or not upload.filename:
            errors.append("professional_photo is required.")
        else:
            try:
                validate_uploaded_file(upload, "image")
                validate_upload_size(
                    upload, current_app.config["MAX_PROFILE_IMAGE_SIZE_MB"]
                )
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

    try:
        account = Parent(
            name=str(data.get("name")).strip(),
            email=email,
            role=account_type,
            is_banned=False,
        )
        account.set_password(str(data.get("password")))
        db.session.add(account)

        if account_type == ROLE_TEACHER:
            db.session.flush()
            profile = TeacherApplication(
                account_id=account.id,
                phone_number=data["phone_number"],
                address=data["address"],
                teacher_type=data["teacher_type"],
                school_name=data["school_name"] if data["teacher_type"] == "school" else None,
                tuition_name=(
                    data["tuition_name"]
                    if data["teacher_type"] == "private_tuition"
                    else None
                ),
                approval_status=APPROVAL_PENDING,
            )
            db.session.add(profile)
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

        if account_type == ROLE_TEACHER:
            return jsonify({
                "message": (
                    "Your teacher registration has been submitted and is waiting "
                    "for administrator approval."
                ),
                "teacher": account.to_dict(),
            }), 202
        return jsonify({
            "message": "Parent account created successfully.",
            "parent": account.to_dict()
        }), 201

    except IntegrityError:
        db.session.rollback()
        if account_type == ROLE_TEACHER and "metadata" in locals():
            _cleanup_registration_upload(metadata)
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
            return jsonify({"error": "This account has been banned. Contact an administrator."}), 403

        approval_error = _teacher_access_error(parent)
        if approval_error:
            return jsonify(approval_error), 403

        login_attempts.reset(account_key)
        access_token = create_access_token(identity=parent)
        refresh_token = create_refresh_token(identity=parent)
        return jsonify(
            {
                "message": "Login successful.",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "parent": parent.to_self_dict(),
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
    approval_error = _teacher_access_error(parent)
    if approval_error:
        return jsonify(approval_error), 403
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
        from app.models.child_access_session_model import ChildAccessSession
        ChildAccessSession.query.filter_by(parent_id=int(get_jwt_identity()), revoked_at=None).update({"revoked_at": now_naive, "revoke_reason":"logout"}, synchronize_session=False)
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
