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


def validate_password(value, *, required=True):
    password = "" if value is None else str(value)
    if required and not password:
        return password, "password is required."
    if not password:
        return password, None
    if len(password) < MIN_PASSWORD_LENGTH:
        return password, f"password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return password, f"password must be {MAX_PASSWORD_LENGTH} characters or fewer."
    return password, None


def is_safe_http_url(value):
    """Accept HTTPS URLs, plus HTTP only for loopback development hosts."""
    url = str(value or "").strip()
    if not url:
        return True
    if len(url) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, UnicodeError):
        return False
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    safe_scheme = scheme == "https" or (
        scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"}
    )

    return (
        safe_scheme
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and (port is None or 1 <= port <= 65535)
    )
