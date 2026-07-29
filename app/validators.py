"""Shared bounds for account and URL input validation."""

from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email


MAX_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 120
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_URL_LENGTH = 500


def validate_name(value, *, required=True):
    name = str(value or "").strip()
    if required and not name:
        return name, "name is required."
    if name and len(name) > MAX_NAME_LENGTH:
        return name, f"name must be {MAX_NAME_LENGTH} characters or fewer."
    return name, None


def validate_account_email(value, *, required=True):
    email = str(value or "").strip()
    if required and not email:
        return email, "email is required."
    if len(email) > MAX_EMAIL_LENGTH:
        return email, f"email must be {MAX_EMAIL_LENGTH} characters or fewer."
    if not email:
        return email, None
    try:
        normalized = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        return email, str(exc)
    if len(normalized) > MAX_EMAIL_LENGTH:
        return normalized, f"email must be {MAX_EMAIL_LENGTH} characters or fewer."
    return normalized, None
