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
    """Detect current and legacy Railway runtime markers."""
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


_DATABASE_URL_ENV_NAMES = (
    "MYSQL_URL",
    "MYSQL_PUBLIC_URL",
    "DATABASE_URL",
)
_DATABASE_VALUE_GROUPS = (
    ("DB_NAME", ("MYSQLDATABASE", "MYSQL_DATABASE", "DB_NAME")),
    ("DB_HOST", ("MYSQLHOST", "DB_HOST")),
    ("DB_PASSWORD", ("MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD", "DB_PASSWORD")),
    ("DB_PORT", ("MYSQLPORT", "DB_PORT")),
    ("DB_USER", ("MYSQLUSER", "DB_USER")),
)


def _configured_database_url():
    """Return the first complete Railway/local database URL."""
    return _env_value(*_DATABASE_URL_ENV_NAMES)


def _missing_database_env_vars():
    """Return missing individual fields when no complete URL is configured."""
    if _configured_database_url():
        return ()
    return tuple(
        display_name
        for display_name, names in _DATABASE_VALUE_GROUPS
        if not _env_value(*names)
    )


def _database_is_configured():
    """Return whether a URL or every required individual value is available."""
    return not _missing_database_env_vars()


def _effective_database_host():
    """Read the selected host without exposing database credentials."""
    database_url = _configured_database_url()
    if database_url:
        _, separator, remainder = database_url.partition("://")
        if separator:
            authority = remainder.split("/", 1)[0].rsplit("@", 1)[-1]
            return authority.rsplit(":", 1)[0].strip("[]")
    return _env_value("MYSQLHOST", "DB_HOST")


def _uses_railway_public_database_proxy():
    """Return whether the selected host uses Railway's public TCP proxy."""
    return _effective_database_host().lower().endswith(".proxy.rlwy.net")


def _build_database_uri():
    """Build a PyMySQL URI from Railway or local database variables.

    Priority:
    1. MYSQL_URL, MYSQL_PUBLIC_URL, or DATABASE_URL.
    2. Railway's individual MYSQL* variables.
    3. Generic DB_* variables used by local development.
    """
    railway_url = _configured_database_url()
    if railway_url:
        scheme, separator, remainder = railway_url.partition("://")
        if not separator or scheme.lower() not in {"mysql", "mysql+pymysql"}:
            raise ValueError(
                "Only MySQL connection URLs are supported. Configure "
                "MYSQL_URL or a mysql:// DATABASE_URL."
            )
        return f"mysql+pymysql://{remainder}"

    db_user = _env_value("MYSQLUSER", "DB_USER", default="root")
    db_password = _env_value(
        "MYSQLPASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "DB_PASSWORD",
        default="root123",
    )
    db_host = _env_value("MYSQLHOST", "DB_HOST", default="localhost")
    db_port = _env_value("MYSQLPORT", "DB_PORT", default="3306")
    db_name = _env_value(
        "MYSQLDATABASE",
        "MYSQL_DATABASE",
        "DB_NAME",
        default="teachalike_db",
    )

    return (
        f"mysql+pymysql://{quote_plus(db_user)}:{quote_plus(db_password)}"
        f"@{db_host}:{db_port}/{quote_plus(db_name)}"
    )


class Config:
    CHILD_ACCESS_SESSION_MINUTES = int(_env_value("CHILD_ACCESS_SESSION_MINUTES", default="30"))
    IS_RAILWAY = _is_railway_environment()
    MISSING_DATABASE_ENV_VARS = _missing_database_env_vars()
    DATABASE_IS_CONFIGURED = not MISSING_DATABASE_ENV_VARS
    DATABASE_USES_RAILWAY_PUBLIC_PROXY = (
        _uses_railway_public_database_proxy()
    )
    DB_USER = _env_value("MYSQLUSER", "DB_USER", default="root")
    DB_PASSWORD = _env_value(
        "MYSQLPASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "DB_PASSWORD",
        default="root123",
    )
    DB_HOST = _env_value("MYSQLHOST", "DB_HOST", default="localhost")
    DB_NAME = _env_value(
        "MYSQLDATABASE",
        "MYSQL_DATABASE",
        "DB_NAME",
        default="teachalike_db",
    )
    DB_PORT = _env_value("MYSQLPORT", "DB_PORT", default="3306")

    SQLALCHEMY_DATABASE_URI = _build_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "connect_args": {"connect_timeout": 5},
    }

    _configured_jwt_secret = _env_value("JWT_SECRET_KEY")
    JWT_SECRET_KEY_IS_EPHEMERAL = not bool(_configured_jwt_secret)
    JWT_SECRET_KEY = _configured_jwt_secret or secrets.token_urlsafe(48)

    # Flask-JWT-Extended reads JWT_ACCESS_TOKEN_EXPIRES specifically.
    _access_token_minutes = _env_value("JWT_ACCESS_TOKEN_EXPIRES_MINUTES")
    JWT_ACCESS_TOKEN_EXPIRES = (
        timedelta(minutes=int(_access_token_minutes))
        if _access_token_minutes
        else timedelta(minutes=15)
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=int(os.getenv("JWT_REFRESH_TOKEN_EXPIRES_DAYS", "30"))
    )
    _frontend_origins = _env_value("FRONTEND_ORIGINS")
    _frontend_origin_values = (_frontend_origins or "*").split(",")
    FRONTEND_ORIGINS = [
        origin.strip().rstrip("/")
        for origin in _frontend_origin_values
        if origin.strip()
    ] or ["*"]
    TRUST_PROXY_HOPS = int(
        _env_value(
            "TRUST_PROXY_HOPS",
            default="1" if IS_RAILWAY else "0",
        )
    )
    _trusted_hosts = _env_value("TRUSTED_HOSTS")
    TRUSTED_HOSTS = (
        [host.strip() for host in _trusted_hosts.split(",") if host.strip()]
        if _trusted_hosts
        else None
    )
    # Railway runs schema setup once through its pre-deploy command. Local
    # development keeps automatic table creation for a convenient first run.
    AUTO_CREATE_TABLES = _boolean_env("AUTO_CREATE_TABLES", not IS_RAILWAY)
    DB_INIT_MAX_ATTEMPTS = _positive_int_env("DB_INIT_MAX_ATTEMPTS", 3)
    DB_INIT_RETRY_SECONDS = _nonnegative_float_env("DB_INIT_RETRY_SECONDS", 1)

    CLOUDINARY_CLOUD_NAME = _env_value("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = _env_value("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = _env_value("CLOUDINARY_API_SECRET")
    CLOUDINARY_ROOT_FOLDER = _env_value(
        "CLOUDINARY_ROOT_FOLDER",
        default="teachalike",
    )
    CLOUDINARY_DELIVERY_TIMEOUT_SECONDS = _positive_int_env(
        "CLOUDINARY_DELIVERY_TIMEOUT_SECONDS",
        60,
    )
    CLOUDINARY_UPLOAD_TIMEOUT_SECONDS = _positive_int_env(
        "CLOUDINARY_UPLOAD_TIMEOUT_SECONDS",
        180,
    )

    # Keep this server-side. Never expose the ElevenLabs key through Next.js
    # public environment variables or return it from an API response.
    ELEVENLABS_API_KEY = _env_value("ELEVENLABS_API_KEY")
    ELEVENLABS_MODEL_ID = _env_value("ELEVENLABS_MODEL_ID", default="eleven_multilingual_v2")
    ELEVENLABS_OUTPUT_FORMAT = _env_value("ELEVENLABS_OUTPUT_FORMAT", default="mp3_44100_128")
    ELEVENLABS_LANGUAGE_CODE = _env_value("ELEVENLABS_LANGUAGE_CODE")
    ELEVENLABS_MAX_CHARS_PER_CHUNK = int(_env_value("ELEVENLABS_MAX_CHARS_PER_CHUNK", default="4500"))
    ELEVENLABS_REQUEST_TIMEOUT = int(_env_value("ELEVENLABS_REQUEST_TIMEOUT", default="120"))
    FFMPEG_BINARY = _env_value("FFMPEG_BINARY")

    GEMINI_API_KEY = _env_value("GEMINI_API_KEY", "GOOGLE_API_KEY")
    GEMINI_MODEL = _env_value("GEMINI_MODEL", default="gemini-2.5-flash")
    GEMINI_REQUEST_TIMEOUT = int(_env_value("GEMINI_REQUEST_TIMEOUT", default="45"))
    # Kimi generates mini-games through NVIDIA NIM's OpenAI-compatible endpoint.
    # KIMI_API_KEY may be used to isolate its credential; NVIDIA_API_KEY/NVAPI_KEY
    # remain accepted for deployments that share one NVIDIA credential.
    KIMI_API_KEY = _env_value("KIMI_API_KEY", "NVIDIA_API_KEY", "NVAPI_KEY")
    KIMI_API_URL = _env_value(
        "KIMI_API_URL",
        default="https://integrate.api.nvidia.com/v1/chat/completions",
    )
    KIMI_MODEL = _env_value("KIMI_MODEL", default="moonshotai/kimi-k2.6")
    KIMI_REQUEST_TIMEOUT = int(_env_value("KIMI_REQUEST_TIMEOUT", default="120"))
    MINI_GAME_GENERATION_RETRIES = 2
    MINI_GAME_REGENERATION_RATE_LIMIT = 10
    MINI_GAME_REGENERATION_WINDOW_SECONDS = 3600

    # Groq model discovery and chat calls stay server-side. NVIDIA/Gemini remain
    # available as legacy provider overrides for existing deployments.
    BOOK_GENERATION_PROVIDER = _env_value("BOOK_GENERATION_PROVIDER", default="groq").lower()
    GROQ_API_KEY = _env_value("GROQ_API_KEY")
    GROQ_API_URL = _env_value("GROQ_API_URL", default="https://api.groq.com/openai/v1")
    GROQ_MODEL = _env_value("GROQ_MODEL", default="openai/gpt-oss-120b")
    GROQ_REQUEST_TIMEOUT = int(_env_value("GROQ_REQUEST_TIMEOUT", default="60"))
    NVIDIA_API_KEY = _env_value("NVIDIA_API_KEY", "NVAPI_KEY")
    NVIDIA_API_URL = _env_value(
        "NVIDIA_API_URL",
        default="https://integrate.api.nvidia.com/v1/chat/completions",
    )
    NVIDIA_MODEL = _env_value("NVIDIA_MODEL", default="openai/gpt-oss-120b")
    # Keep the upstream AI call below the Gunicorn/platform request window so
    # clients receive a useful error instead of waiting until the connection
    # is terminated by the deployment proxy.
    NVIDIA_REQUEST_TIMEOUT = int(_env_value("NVIDIA_REQUEST_TIMEOUT", default="120"))

    # NVIDIA ASR is used server-side for pronunciation recordings. Keep this
    # separate so the hosted ASR endpoint can differ from chat completions.
    NVIDIA_ASR_API_KEY = _env_value("NVIDIA_ASR_API_KEY", "NVIDIA_API_KEY", "NVAPI_KEY")
    NVIDIA_ASR_API_URL = _env_value(
        "NVIDIA_ASR_API_URL",
        default="https://1598d209-5e27-4d3c-8079-4751568b1081.invocation.api.nvcf.nvidia.com/v1/audio/transcriptions",
    )
    NVIDIA_ASR_LANGUAGE = _env_value("NVIDIA_ASR_LANGUAGE", default="en-US")
    NVIDIA_ASR_REQUEST_TIMEOUT = int(_env_value("NVIDIA_ASR_REQUEST_TIMEOUT", default="45"))
    NVIDIA_PRONUNCIATION_API_KEY = _env_value(
        "NVIDIA_PRONUNCIATION_API_KEY",
        "NVIDIA_ASR_API_KEY",
        "NVIDIA_API_KEY",
        "NVAPI_KEY",
    )
    NVIDIA_PRONUNCIATION_REQUEST_TIMEOUT = int(
        _env_value("NVIDIA_PRONUNCIATION_REQUEST_TIMEOUT", default="20")
    )

    VOSK_MODEL_PATH = os.getenv(
        "VOSK_MODEL_PATH",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "vosk-model-small-en-us-0.15"),
    )

    # Must accommodate the largest supported upload endpoint. Individual
    # routes still enforce their narrower per-asset limits below.
    MAX_CONTENT_LENGTH = (
        _positive_int_env("MAX_CONTENT_LENGTH_MB", 1000) * 1024 * 1024
    )

    # Per-asset limits are kept separate from Flask's request-wide limit so
    # each upload endpoint can return the correct validation response.
    MAX_PROFILE_IMAGE_SIZE_MB = _positive_int_env(
        "MAX_PROFILE_IMAGE_SIZE_MB", 10
    )
    MAX_CHILD_IMAGE_SIZE_MB = _positive_int_env(
        "MAX_CHILD_IMAGE_SIZE_MB", 10
    )
    MAX_VOICE_PROFILE_SIZE_MB = _positive_int_env(
        "MAX_VOICE_PROFILE_SIZE_MB", 50
    )
    MAX_BOOK_AUDIO_SIZE_MB = _positive_int_env(
        "MAX_BOOK_AUDIO_SIZE_MB", 250
    )
    MAX_BOOK_VIDEO_SIZE_MB = _positive_int_env(
        "MAX_BOOK_VIDEO_SIZE_MB", 1000
    )
    MAX_JSON_BODY_SIZE_BYTES = int(
        _env_value("MAX_JSON_BODY_SIZE_BYTES", default=str(1024 * 1024))
    )
    LOGIN_RATE_LIMIT_ATTEMPTS = int(
        _env_value("LOGIN_RATE_LIMIT_ATTEMPTS", default="10")
    )
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(
        _env_value("LOGIN_RATE_LIMIT_WINDOW_SECONDS", default="900")
    )
    REGISTER_RATE_LIMIT_ATTEMPTS = int(
        _env_value("REGISTER_RATE_LIMIT_ATTEMPTS", default="20")
    )
    REGISTER_RATE_LIMIT_WINDOW_SECONDS = int(
        _env_value("REGISTER_RATE_LIMIT_WINDOW_SECONDS", default="3600")
    )
    PRONUNCIATION_RATE_LIMIT_ATTEMPTS = int(
        _env_value("PRONUNCIATION_RATE_LIMIT_ATTEMPTS", default="60")
    )
    PRONUNCIATION_RATE_LIMIT_WINDOW_SECONDS = int(
        _env_value("PRONUNCIATION_RATE_LIMIT_WINDOW_SECONDS", default="3600")
    )
    PIN_RATE_LIMIT_ATTEMPTS = int(
        _env_value("PIN_RATE_LIMIT_ATTEMPTS", default="5")
    )
    PIN_RATE_LIMIT_WINDOW_SECONDS = int(
        _env_value("PIN_RATE_LIMIT_WINDOW_SECONDS", default="300")
    )
    ACCOUNT_PASSWORD_RATE_LIMIT_ATTEMPTS = int(
        _env_value("ACCOUNT_PASSWORD_RATE_LIMIT_ATTEMPTS", default="5")
    )
    ACCOUNT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS = int(
        _env_value("ACCOUNT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS", default="300")
    )
