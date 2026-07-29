from datetime import timedelta
import os
import secrets
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _env_value(*names, default=""):
    """Read the first usable environment value and strip accidental quotes."""
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()

        if value and "${{" not in value and "}}" not in value:
            return value
    return default


def _positive_int_env(name, default):
    """Read a positive integer setting with a clear startup error."""
    raw_value = _env_value(name, default=str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _nonnegative_float_env(name, default):
    """Read a non-negative numeric setting with a clear startup error."""
    raw_value = _env_value(name, default=str(default))
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number.") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative number.")
    return value


def _boolean_env(name, default):
    """Read a conventional true/false environment setting."""
    raw_value = _env_value(name, default="true" if default else "false").lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _is_railway_environment():
    """Detect current and legacy Railway runtime environment markers."""
    return any(
        _env_value(name)
        for name in (
            "RAILWAY_ENVIRONMENT_ID",
            "RAILWAY_ENVIRONMENT_NAME",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_ENVIRONMENT",
        )
    )


def _build_database_uri():
    """Build the SQLAlchemy MySQL URI from whatever Railway (or local .env)
    variables are actually available.

    Priority:
    1. A full connection URL: MYSQL_URL / DATABASE_URL / MYSQL_PUBLIC_URL.
       Railway's MySQL plugin exposes ``MYSQL_URL`` as a service reference,
       e.g. ``MYSQL_URL=${{MySQL.MYSQL_URL}}`` on the API service.
    2. Railway's individual MYSQL* variables (MYSQLHOST, MYSQLPORT, ...).
    3. The generic DB_* variables (used for local development).
    """

    # Prefer private/internal URLs over the public TCP proxy when both are
    # present on Railway.
    railway_url = _env_value("MYSQL_URL", "DATABASE_URL", "MYSQL_PUBLIC_URL")
    if railway_url:
        scheme, separator, remainder = railway_url.partition("://")
        if not separator or scheme.lower() not in {"mysql", "mysql+pymysql"}:
            raise ValueError(
                "Only MySQL connection URLs are supported. Configure MYSQL_URL "
                "or a mysql:// DATABASE_URL."
            )
        return f"mysql+pymysql://{remainder}"

    db_user = _env_value("MYSQLUSER", "DB_USER", default="root")
    db_password = _env_value(
        "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD", "DB_PASSWORD", default="root123"
    )
    db_host = _env_value("MYSQLHOST", "DB_HOST", default="localhost")
    db_port = _env_value("MYSQLPORT", "DB_PORT", default="3306")
    db_name = _env_value(
        "MYSQLDATABASE", "MYSQL_DATABASE", "DB_NAME", default="teachalike_db"
    )

    return (
        f"mysql+pymysql://{quote_plus(db_user)}:{quote_plus(db_password)}"
        f"@{db_host}:{db_port}/{quote_plus(db_name)}"
    )
