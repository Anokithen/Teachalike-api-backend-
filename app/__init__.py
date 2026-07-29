import time

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy import inspect, text

from app.config import Config
from app.extensions import db, jwt
from app.routes import register_blueprints


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    _validate_runtime_config(app)
    proxy_hops = app.config["TRUST_PROXY_HOPS"]
    if proxy_hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
        )
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["FRONTEND_ORIGINS"]}},
        allow_headers=["Authorization", "Content-Type"],
        methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    )
    db.init_app(app)
    jwt.init_app(app)

    @app.before_request
    def reject_oversized_json():
        if (
            request.is_json
            and request.content_length is not None
            and request.content_length > app.config["MAX_JSON_BODY_SIZE_BYTES"]
        ):
            return jsonify({"error": "The JSON request body is too large."}), 413
        if request.is_json and request.method in {"POST", "PATCH", "PUT"}:
            payload = request.get_json(silent=True)
            if payload is None:
                return jsonify({"error": "The JSON request body is invalid."}), 400
            if not isinstance(payload, dict):
                return jsonify({"error": "The JSON request body must be an object."}), 400

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=(self)",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if request.path.startswith("/api/auth/") or request.headers.get("Authorization"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    from app.models import Parent, RevokedToken  # noqa: F401  (this import loads app/models/__init__.py,
    # which in turn imports every model class so they register with SQLAlchemy)

    with app.app_context():
        _prepare_database(app)

    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        return db.session.get(Parent, int(identity))

    @jwt.user_identity_loader
    def user_identity_lookup(parent):
        # allows create_access_token(identity=parent) or identity=parent.id
        return str(parent.id) if hasattr(parent, "id") else str(parent)

    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(_jwt_header, jwt_payload):
        if RevokedToken.query.filter_by(jti=jwt_payload["jti"]).first() is not None:
            return True

        # A banned account's outstanding tokens are treated as revoked too,
        # so a ban takes effect immediately instead of waiting for expiry.
        identity = jwt_payload.get("sub")
        if identity is not None:
            parent = db.session.get(Parent, int(identity))
            if parent is None or parent.is_banned:
                return True
        return False

    register_blueprints(app)

    @app.get("/health")
    def health_check():
        return jsonify({"status": "ok"}), 200

    @app.get("/health/ready")
    def readiness_check():
        try:
            _verify_database_ready()
        except Exception as exc:
            db.session.rollback()
            app.logger.warning("Database readiness check failed: %s", exc)
            return jsonify({"status": "unavailable", "database": "unavailable"}), 503
        return jsonify({"status": "ok", "database": "ready"}), 200

    @app.errorhandler(OperationalError)
    def handle_operational_error(err):
        db.session.rollback()
        orig = getattr(err, "orig", None)
        code = orig.args[0] if orig and orig.args else None
        if code == 1049:
            return jsonify({"error": "Invalid database name configured."}), 500
        if code in (2003, 2002):
            return jsonify({"error": "MySQL server is not running or not reachable."}), 503
        return jsonify({"error": "Database connection failed."}), 500

    @app.errorhandler(ProgrammingError)
    def handle_programming_error(err):
        db.session.rollback()
        return jsonify({"error": "Invalid database name configured."}), 500

    @app.errorhandler(404)
    def handle_not_found(err):
        return jsonify({"error": "Resource not found."}), 404

    @app.errorhandler(500)
    def handle_internal_error(err):
        return jsonify({"error": "An internal server error occurred."}), 500

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(err):
        limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
        return jsonify({"error": f"The uploaded file must be smaller than {limit_mb} MB."}), 413

    return app


def _validate_runtime_config(app):
    """Fail early when a Railway deployment is missing required secrets."""
    if not app.config["IS_RAILWAY"]:
        return
    if not app.config["DATABASE_IS_CONFIGURED"]:
        raise RuntimeError(
            "Railway database configuration is missing. Reference MYSQL_URL "
            "from the MySQL service on the API service."
        )
    if app.config["JWT_SECRET_KEY_IS_EPHEMERAL"]:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set to a stable secret on Railway."
        )
    if len(app.config["JWT_SECRET_KEY"]) < 32:
        raise RuntimeError("JWT_SECRET_KEY must contain at least 32 characters.")


def _prepare_database(app):
    """Connect, create/upgrade the schema, and retry brief Railway races."""
    attempts = app.config["DATABASE_STARTUP_MAX_ATTEMPTS"]
    retry_seconds = app.config["DATABASE_STARTUP_RETRY_SECONDS"]

    for attempt in range(1, attempts + 1):
        try:
            if app.config["AUTO_CREATE_TABLES"]:
                db.create_all()
                _ensure_voice_profile_schema()
                _ensure_profile_image_schema()
                _ensure_book_schema()
                _ensure_book_narration_schema()
            _verify_database_ready()
            return
        except Exception as exc:
            db.session.rollback()
            if attempt == attempts:
                raise RuntimeError(
                    f"Database initialization failed after {attempts} attempt(s)."
                ) from exc
            app.logger.warning(
                "Database initialization attempt %s/%s failed; retrying in %ss: %s",
                attempt,
                attempts,
                retry_seconds,
                exc,
            )
            time.sleep(retry_seconds)
