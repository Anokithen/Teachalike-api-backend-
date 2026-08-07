import hashlib
import secrets

from flask import current_app

from app.extensions import db
from app.models.email_delivery_model import EMAIL_STATUS_PENDING, EMAIL_TYPE_VERIFY_ACCOUNT, EmailDelivery
from app.models.email_verification_token_model import PURPOSE_EMAIL_VERIFICATION, EmailVerificationToken
from app.security import anonymized_key
from app.utils import utc_now


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def mask_email(email):
    local, _, domain = str(email).partition("@")
    if not domain:
        return "***"
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:2] + "***" + local[-1:]
    return f"{masked_local}@{domain}"


def verification_url(raw_token):
    base = current_app.config["FRONTEND_URL"].rstrip("/")
    return f"{base}/verify-email?token={raw_token}"


def create_verification_token_and_event(account, request_obj=None):
    now = utc_now()
    EmailVerificationToken.query.filter(
        EmailVerificationToken.account_id == account.id,
        EmailVerificationToken.purpose == PURPOSE_EMAIL_VERIFICATION,
        EmailVerificationToken.used_at.is_(None),
        EmailVerificationToken.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
    raw_token = secrets.token_urlsafe(48)
    token = EmailVerificationToken(
        account_id=account.id,
        token_hash=hash_token(raw_token),
        purpose=PURPOSE_EMAIL_VERIFICATION,
        expires_at=now + current_app.config["EMAIL_VERIFICATION_DELTA"],
        request_ip_hash=anonymized_key("verify-ip", getattr(request_obj, "remote_addr", "") or "unknown") if request_obj else None,
        user_agent_hash=anonymized_key("verify-ua", getattr(getattr(request_obj, "user_agent", None), "string", "") or "unknown") if request_obj else None,
    )
    db.session.add(token)
    db.session.flush()
    delivery = EmailDelivery(
        recipient_account_id=account.id,
        recipient_email=account.email,
        email_type=EMAIL_TYPE_VERIFY_ACCOUNT,
        event_key=f"email_verification:{token.id}",
        status=EMAIL_STATUS_PENDING,
        context_json={"token_id": token.id},
    )
    db.session.add(delivery)
    return raw_token, delivery
