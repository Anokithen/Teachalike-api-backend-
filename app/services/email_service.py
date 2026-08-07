import base64
import html
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import requests
from flask import current_app

from app.models.email_delivery_model import (
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_RETRY,
    EMAIL_STATUS_SENT,
    EMAIL_STATUS_SENDING,
    EMAIL_TYPE_TEACHER_APPROVED,
    EMAIL_TYPE_VERIFY_ACCOUNT,
    EmailDelivery,
)
from app.utils import utc_now


class EmailConfigError(RuntimeError):
    pass


@dataclass
class EmailSendResult:
    ok: bool
    status: str
    provider_message_id: str | None = None
    error_code: str | None = None


def validate_mail_config(config):
    transport = config.get("MAIL_TRANSPORT", "disabled")
    if transport in {"disabled", "console"}:
        return
    required = ["MAIL_FROM_EMAIL"]
    if transport == "gmail_api":
        required += ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]
    elif transport == "gmail_smtp":
        required += ["GMAIL_SMTP_USERNAME", "GMAIL_SMTP_APP_PASSWORD", "GMAIL_SMTP_HOST", "GMAIL_SMTP_PORT"]
    else:
        raise EmailConfigError("MAIL_TRANSPORT must be disabled, console, gmail_api, or gmail_smtp.")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise EmailConfigError("Mail configuration is missing: " + ", ".join(missing))


def _safe_address(value):
    address = str(value or "").strip()
    if "\n" in address or "\r" in address:
        raise ValueError("Email header values cannot contain newlines.")
    return address


def _message(to_email, subject, text_body, html_body=None):
    config = current_app.config
    sender_name = _safe_address(config["MAIL_FROM_NAME"])
    sender_email = _safe_address(config["MAIL_FROM_EMAIL"])
    msg = EmailMessage()
    msg["To"] = _safe_address(to_email)
    msg["From"] = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    msg["Subject"] = _safe_address(subject)
    if config.get("MAIL_REPLY_TO"):
        msg["Reply-To"] = _safe_address(config["MAIL_REPLY_TO"])
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def _gmail_api_access_token():
    config = current_app.config
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": config["GMAIL_CLIENT_ID"],
            "client_secret": config["GMAIL_CLIENT_SECRET"],
            "refresh_token": config["GMAIL_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=config["MAIL_CONNECT_TIMEOUT_SECONDS"],
    )
    if response.status_code >= 400:
        raise RuntimeError("gmail_token_error")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("gmail_token_error")
    return token


def send_plain_email(to_email, subject, text_body, html_body=None):
    transport = current_app.config.get("MAIL_TRANSPORT", "disabled")
    if transport == "disabled":
        return EmailSendResult(False, EMAIL_STATUS_FAILED, error_code="MAIL_DISABLED")
    if transport == "console":
        current_app.logger.info("Console mail accepted type=transactional recipient_hash=%s", hash(to_email))
        return EmailSendResult(True, EMAIL_STATUS_SENT, provider_message_id="console")
    try:
        msg = _message(to_email, subject, text_body, html_body)
        if transport == "gmail_api":
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            token = _gmail_api_access_token()
            response = requests.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"raw": raw},
                timeout=current_app.config["MAIL_SEND_TIMEOUT_SECONDS"],
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                return EmailSendResult(False, EMAIL_STATUS_RETRY, error_code="TEMPORARY_PROVIDER_FAILURE")
            if response.status_code >= 400:
                return EmailSendResult(False, EMAIL_STATUS_FAILED, error_code="PERMANENT_PROVIDER_FAILURE")
            return EmailSendResult(True, EMAIL_STATUS_SENT, provider_message_id=response.json().get("id"))
        if transport == "gmail_smtp":
            with smtplib.SMTP(
                current_app.config["GMAIL_SMTP_HOST"],
                int(current_app.config["GMAIL_SMTP_PORT"]),
                timeout=current_app.config["MAIL_CONNECT_TIMEOUT_SECONDS"],
            ) as server:
                server.starttls(timeout=current_app.config["MAIL_SEND_TIMEOUT_SECONDS"])
                server.login(current_app.config["GMAIL_SMTP_USERNAME"], current_app.config["GMAIL_SMTP_APP_PASSWORD"])
                server.send_message(msg)
            return EmailSendResult(True, EMAIL_STATUS_SENT, provider_message_id="smtp")
        return EmailSendResult(False, EMAIL_STATUS_FAILED, error_code="MAIL_TRANSPORT_INVALID")
    except (requests.Timeout, requests.ConnectionError, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
        return EmailSendResult(False, EMAIL_STATUS_RETRY, error_code="TEMPORARY_PROVIDER_FAILURE")
    except Exception:
        current_app.logger.exception("Transactional email send failed without exposing provider response.")
        return EmailSendResult(False, EMAIL_STATUS_FAILED, error_code="MAIL_SEND_FAILED")


def verification_email(account, verification_url):
    name = html.escape(account.name or "there")
    safe_url = html.escape(verification_url, quote=True)
    text = (
        f"Hi {account.name},\n\n"
        "Please verify your email address to finish creating your TeachAlike account.\n\n"
        f"{verification_url}\n\n"
        "This link expires soon. If you did not create this account, you can ignore this email."
    )
    html_body = (
        f"<p>Hi {name},</p>"
        "<p>Please verify your email address to finish creating your TeachAlike account.</p>"
        f'<p><a href="{safe_url}">Verify your email</a></p>'
        "<p>This link expires soon. If you did not create this account, you can ignore this email.</p>"
    )
    return send_plain_email(account.email, "Verify your TeachAlike email address", text, html_body)


def teacher_approval_email(account):
    login_url = current_app.config["FRONTEND_URL"].rstrip("/") + "/login"
    name = html.escape(account.name or "Teacher")
    safe_login_url = html.escape(login_url, quote=True)
    text = (
        f"Hi {account.name},\n\n"
        "Great news! Your TeachAlike teacher application has been approved. "
        "You can now sign in and start creating learning experiences for children.\n\n"
        f"Sign in: {login_url}\n\n"
        "Reply to this email if you need support."
    )
    html_body = (
        f"<p>Hi {name},</p>"
        "<p>Great news! Your TeachAlike teacher application has been approved. "
        "You can now sign in and start creating learning experiences for children.</p>"
        f'<p><a href="{safe_login_url}">Sign in to TeachAlike</a></p>'
        "<p>Reply to this email if you need support.</p>"
    )
    return send_plain_email(account.email, "Your TeachAlike teacher account has been approved", text, html_body)


def send_delivery(delivery, *, verification_url=None):
    delivery.status = EMAIL_STATUS_SENDING
    delivery.attempt_count = (delivery.attempt_count or 0) + 1
    result = None
    if delivery.email_type == EMAIL_TYPE_VERIFY_ACCOUNT:
        if not verification_url:
            result = EmailSendResult(False, EMAIL_STATUS_FAILED, error_code="VERIFICATION_LINK_UNAVAILABLE")
        else:
            result = verification_email(delivery.recipient_account, verification_url)
    elif delivery.email_type == EMAIL_TYPE_TEACHER_APPROVED:
        result = teacher_approval_email(delivery.recipient_account)
    else:
        result = EmailSendResult(False, EMAIL_STATUS_FAILED, error_code="UNKNOWN_EMAIL_TYPE")

    delivery.status = result.status
    delivery.provider_message_id = result.provider_message_id
    delivery.last_error_code = result.error_code
    if result.ok:
        delivery.sent_at = utc_now()
        delivery.next_attempt_at = None
    elif result.status == EMAIL_STATUS_RETRY and delivery.attempt_count < current_app.config["MAIL_MAX_ATTEMPTS"]:
        delay = min(3600, 60 * (2 ** max(0, delivery.attempt_count - 1)))
        delivery.next_attempt_at = utc_now() + current_app.config["MAIL_RETRY_DELTA_FACTORY"](delay)
    else:
        delivery.status = EMAIL_STATUS_FAILED
        delivery.next_attempt_at = None
    return result
