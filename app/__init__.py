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


TEACHER_APPLICATION_REQUIRED_COLUMNS = frozenset({
    "id",
    "account_id",
    "phone_number",
    "address",
    "teacher_type",
    "school_name",
    "tuition_name",
    "approval_status",
    "reviewed_by_id",
    "reviewed_at",
    "rejection_reason",
    "created_at",
    "updated_at",
})
MYSQL_SCHEMA_NOT_READY_CODES = frozenset({1054, 1146})
DATABASE_SCHEMA_NOT_READY_PAYLOAD = {
    "error": "The database schema is not ready. Run the database migration.",
    "error_code": "DATABASE_SCHEMA_NOT_READY",
}
TEACHER_APPLICATION_COLUMN_DEFINITIONS = {
    "id": "INTEGER NULL",
    "account_id": "INTEGER NULL",
    "phone_number": "VARCHAR(40) NULL",
    "address": "VARCHAR(500) NULL",
    "teacher_type": "VARCHAR(30) NULL",
    "school_name": "VARCHAR(200) NULL",
    "tuition_name": "VARCHAR(200) NULL",
    "approval_status": "VARCHAR(20) NULL DEFAULT 'pending'",
    "reviewed_by_id": "INTEGER NULL",
    "reviewed_at": "DATETIME NULL",
    "rejection_reason": "VARCHAR(1000) NULL",
    "created_at": "DATETIME NULL",
    "updated_at": "DATETIME NULL",
}
TEACHER_APPLICATION_COPY_COLUMNS = (
    "account_id",
    "phone_number",
    "address",
    "teacher_type",
    "school_name",
    "tuition_name",
    "approval_status",
    "reviewed_by_id",
    "reviewed_at",
    "rejection_reason",
    "created_at",
    "updated_at",
)


def create_app(*, initialize_database=None):
    """Create the API app without blocking Railway web-worker startup."""
    app = Flask(__name__)
    app.config.from_object(Config)
    _validate_deployment_config(app)
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
        allow_headers=["Authorization", "Content-Type", "X-Child-Session"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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

    if _should_initialize_database(app, initialize_database):
        with app.app_context():
            _initialize_database_schema(app)

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
            if (
                parent.is_teacher
                and parent.teacher_application is not None
                and parent.teacher_application.approval_status != "approved"
            ):
                return True
        return False

    @jwt.revoked_token_loader
    def revoked_token_callback(_jwt_header, jwt_payload):
        """Keep approval and ban failures explicit when old tokens are blocked."""
        identity = jwt_payload.get("sub")
        account = db.session.get(Parent, int(identity)) if identity is not None else None
        if account and account.is_banned:
            return jsonify({
                "error": "This account has been banned. Contact an administrator.",
            }), 403
        if account and account.is_teacher and account.teacher_application is not None:
            profile = account.teacher_application
            if profile.approval_status == "pending":
                return jsonify({
                    "error": "Your teacher account is waiting for administrator approval.",
                    "error_code": "TEACHER_APPROVAL_PENDING",
                }), 403
            if profile.approval_status == "rejected":
                payload = {
                    "error": "Your teacher registration was rejected by an administrator.",
                    "error_code": "TEACHER_APPROVAL_REJECTED",
                }
                if profile.rejection_reason:
                    payload["rejection_reason"] = profile.rejection_reason
                return jsonify(payload), 403
        return jsonify({"error": "Token has been revoked."}), 401

    register_blueprints(app)

    @app.get("/health")
    def health_check():
        return jsonify({"status": "ok"}), 200

    @app.get("/health/ready")
    def readiness_check():
        try:
            _verify_database_schema()
        except Exception as exc:
            db.session.rollback()
            app.logger.warning("Database readiness check failed: %s", exc)
            return jsonify({"status": "unavailable", "database": "unavailable"}), 503
        return jsonify({"status": "ok", "database": "ready"}), 200

    @app.errorhandler(OperationalError)
    def handle_operational_error(err):
        db.session.rollback()
        code = _database_error_code(err)
        app.logger.exception("Database operational error while handling a request")
        if code in MYSQL_SCHEMA_NOT_READY_CODES:
            return jsonify(DATABASE_SCHEMA_NOT_READY_PAYLOAD), 503
        if code == 1049:
            return jsonify({
                "error": "The configured database is unavailable.",
                "error_code": "DATABASE_CONFIGURATION_ERROR",
            }), 500
        if code in (2003, 2002):
            return jsonify({
                "error": "The database is temporarily unavailable.",
                "error_code": "DATABASE_UNAVAILABLE",
            }), 503
        return jsonify({
            "error": "A database operation failed.",
            "error_code": "DATABASE_OPERATION_FAILED",
        }), 500

    @app.errorhandler(ProgrammingError)
    def handle_programming_error(err):
        db.session.rollback()
        code = _database_error_code(err)
        app.logger.exception("Database programming error while handling a request")
        if code in MYSQL_SCHEMA_NOT_READY_CODES:
            return jsonify(DATABASE_SCHEMA_NOT_READY_PAYLOAD), 503
        return jsonify({
            "error": "A database query could not be completed.",
            "error_code": "DATABASE_QUERY_FAILED",
        }), 500

    @app.errorhandler(404)
    def handle_not_found(err):
        return jsonify({"error": "Resource not found."}), 404

    @app.errorhandler(500)
    def handle_internal_error(err):
        app.logger.exception("Unexpected internal server error")
        return jsonify({"error": "An internal server error occurred."}), 500

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(err):
        limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
        return jsonify({"error": f"The uploaded file must be smaller than {limit_mb} MB."}), 413

    return app


def _should_initialize_database(app, initialize_database):
    """Run schema setup locally or when the pre-deploy command requests it."""
    if initialize_database is not None:
        return bool(initialize_database)
    return bool(
        app.config["AUTO_CREATE_TABLES"]
        and not app.config["IS_RAILWAY"]
    )


def _validate_deployment_config(app):
    """Reject incomplete production configuration before serving traffic."""
    if not app.config["IS_RAILWAY"]:
        return
    if not app.config["DATABASE_IS_CONFIGURED"]:
        missing_variables = app.config.get(
            "MISSING_DATABASE_ENV_VARS",
            ("DB_NAME", "DB_HOST", "DB_PASSWORD", "DB_PORT", "DB_USER"),
        )
        raise RuntimeError(
            "Database configuration is missing. Set MYSQL_URL or provide "
            "the required individual variable(s): "
            f"{', '.join(missing_variables)}."
        )
    if app.config["DATABASE_USES_RAILWAY_PUBLIC_PROXY"]:
        app.logger.warning(
            "The database connection uses Railway's public TCP proxy. It is "
            "supported, but MYSQL_URL or MYSQLHOST should reference private "
            "networking when both services are in the same Railway project."
        )
    if app.config["JWT_SECRET_KEY_IS_EPHEMERAL"]:
        raise RuntimeError("JWT_SECRET_KEY must be set to a stable secret.")
    jwt_secret = app.config["JWT_SECRET_KEY"]
    if (
        len(jwt_secret) < 32
        or jwt_secret.lower().startswith("replace-with-")
        or jwt_secret.lower().startswith("change-me")
    ):
        raise RuntimeError(
            "JWT_SECRET_KEY must be a unique secret containing at least "
            "32 characters, not an example placeholder."
        )
    if app.config["FRONTEND_ORIGINS"] == ["*"]:
        raise RuntimeError(
            "FRONTEND_ORIGINS must be set to the deployed frontend origin."
        )


def _initialize_database_schema(app):
    """Create all model tables, retrying transient database failures."""
    max_attempts = app.config["DB_INIT_MAX_ATTEMPTS"]
    retry_seconds = app.config["DB_INIT_RETRY_SECONDS"]

    for attempt in range(1, max_attempts + 1):
        stage = "database connection"
        try:
            db.session.execute(text("SELECT 1"))
            stage = "teacher application table migration"
            _prepare_teacher_application_table()
            stage = "model table creation"
            db.create_all()
            stage = "voice profile schema compatibility"
            _ensure_voice_profile_schema()
            stage = "profile image schema compatibility"
            _ensure_profile_image_schema()
            stage = "teacher application compatibility and backfill"
            _ensure_teacher_application_schema()
            stage = "book schema compatibility"
            _ensure_book_schema()
            stage = "book asset ownership compatibility"
            _ensure_book_asset_owner_schema()
            stage = "book narration schema compatibility"
            _ensure_book_narration_schema()
            stage = "mini-game generation schema compatibility"
            _ensure_mini_game_generation_schema()
            stage = "schema verification"
            _verify_database_schema()
            app.logger.info(
                "Database schema initialized successfully with %s tables.",
                len(db.metadata.tables),
            )
            return
        except Exception as exc:
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()
            if attempt == max_attempts:
                raise RuntimeError(
                    "Database schema initialization failed during "
                    f"{stage} after {max_attempts} attempt(s)."
                ) from exc
            app.logger.warning(
                "Database initialization attempt %s/%s failed during %s; "
                "retrying in %ss: %s",
                attempt,
                max_attempts,
                stage,
                retry_seconds,
                exc,
            )
            time.sleep(retry_seconds)


def _verify_database_schema():
    """Verify database connectivity plus required tables and critical columns."""
    db.session.execute(text("SELECT 1"))
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(db.metadata.tables)
    missing_tables = sorted(expected_tables - existing_tables)
    if missing_tables:
        raise RuntimeError(
            "Required tables are missing: " + ", ".join(missing_tables)
        )
    application_columns = {
        column["name"]
        for column in inspector.get_columns("teacher_applications")
    }
    missing_application_columns = sorted(
        TEACHER_APPLICATION_REQUIRED_COLUMNS - application_columns
    )
    if missing_application_columns:
        raise RuntimeError(
            "teacher_applications is missing required columns: "
            + ", ".join(missing_application_columns)
        )


def _database_error_code(err):
    """Return a numeric MySQL error code without exposing driver messages."""
    original = getattr(err, "orig", None)
    args = getattr(original, "args", ())
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _ensure_voice_profile_schema():
    """Add the ElevenLabs ID to databases created before voice cloning support."""
    inspector = inspect(db.engine)
    if not inspector.has_table("voice_profiles"):
        return
    columns = {column["name"] for column in inspector.get_columns("voice_profiles")}
    if "elevenlabs_voice_id" in columns:
        return
    db.session.execute(
        text("ALTER TABLE voice_profiles ADD COLUMN elevenlabs_voice_id VARCHAR(255) NULL")
    )
    db.session.execute(
        text(
            "CREATE UNIQUE INDEX uq_voice_profiles_elevenlabs_voice_id "
            "ON voice_profiles (elevenlabs_voice_id)"
        )
    )
    db.session.commit()


def _ensure_book_schema():
    """Idempotently add book media and creator-attribution columns."""
    inspector = inspect(db.engine)
    if not inspector.has_table("books"):
        return
    columns = {column["name"] for column in inspector.get_columns("books")}
    additions = {
        "image_urls": "JSON NULL",
        "description": "TEXT NULL",
        "created_by_account_id": "INTEGER NULL",
        "creator_name_snapshot": "VARCHAR(120) NULL",
        "creation_request_id": "VARCHAR(64) NULL",
        "asset_root_folder": "VARCHAR(500) NULL",
        "updated_at": "DATETIME NULL",
    }
    changed = False
    for name, sql_type in additions.items():
        if name not in columns:
            db.session.execute(text(f"ALTER TABLE books ADD COLUMN {name} {sql_type}"))
            changed = True
    if "updated_at" not in columns:
        db.session.execute(text("UPDATE books SET updated_at = created_at WHERE updated_at IS NULL"))
    if db.engine.dialect.name == "mysql":
        updated_column = next(
            column
            for column in inspect(db.engine).get_columns("books")
            if column["name"] == "updated_at"
        )
        if updated_column.get("nullable", True):
            db.session.execute(text(
                "ALTER TABLE books MODIFY updated_at DATETIME NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
            ))
            changed = True

    inspector = inspect(db.engine)
    indexes = {index["name"] for index in inspector.get_indexes("books")}
    if "ix_books_created_by_account_id" not in indexes:
        db.session.execute(text(
            "CREATE INDEX ix_books_created_by_account_id ON books (created_by_account_id)"
        ))
        changed = True
    if "uq_books_creator_request" not in indexes:
        db.session.execute(text(
            "CREATE UNIQUE INDEX uq_books_creator_request "
            "ON books (created_by_account_id, creation_request_id)"
        ))
        changed = True
    if db.engine.dialect.name == "mysql":
        foreign_keys = inspect(db.engine).get_foreign_keys("books")
        has_creator_fk = any(
            fk.get("constrained_columns") == ["created_by_account_id"]
            and fk.get("referred_table") == "parents"
            for fk in foreign_keys
        )
        if not has_creator_fk:
            db.session.execute(text(
                "ALTER TABLE books ADD CONSTRAINT fk_books_created_by_account "
                "FOREIGN KEY (created_by_account_id) REFERENCES parents(id) ON DELETE SET NULL"
            ))
            changed = True
    if changed:
        db.session.commit()
    from app.models.book_model import Book
    from app.services.book_management_service import ensure_book_asset_root
    roots_changed = False
    for book in Book.query.filter(Book.asset_root_folder.is_(None)).all():
        ensure_book_asset_root(book)
        roots_changed = True
    if roots_changed:
        db.session.commit()


def _ensure_book_asset_owner_schema():
    """Preserve authored-book ledger rows when their teacher is deleted."""
    inspector = inspect(db.engine)
    if not inspector.has_table("assets") or db.engine.dialect.name != "mysql":
        return
    owner_column = next(
        column for column in inspector.get_columns("assets")
        if column["name"] == "owner_user_id"
    )
    owner_fk = next((
        fk for fk in inspector.get_foreign_keys("assets")
        if fk.get("constrained_columns") == ["owner_user_id"]
        and fk.get("referred_table") == "parents"
    ), None)
    ondelete = str((owner_fk or {}).get("options", {}).get("ondelete") or "").upper()
    if not owner_column.get("nullable", True) or ondelete != "SET NULL":
        if owner_fk and owner_fk.get("name"):
            constraint_name = owner_fk["name"]
            if not constraint_name.replace("_", "").isalnum():
                raise RuntimeError("Unexpected assets owner constraint name.")
            db.session.execute(text(
                f"ALTER TABLE assets DROP FOREIGN KEY `{constraint_name}`"
            ))
        db.session.execute(text(
            "ALTER TABLE assets MODIFY owner_user_id INTEGER NULL"
        ))
        db.session.execute(text(
            "ALTER TABLE assets ADD CONSTRAINT fk_assets_owner "
            "FOREIGN KEY (owner_user_id) REFERENCES parents(id) ON DELETE SET NULL"
        ))
        db.session.commit()


def _ensure_book_narration_schema():
    """Upgrade narration metadata and remove the retired cache constraint."""
    inspector = inspect(db.engine)
    if not inspector.has_table("book_narrations"):
        return
    columns = {
        column["name"] for column in inspector.get_columns("book_narrations")
    }
    changed = False
    if "language" not in columns:
        db.session.execute(
            text("ALTER TABLE book_narrations ADD COLUMN language VARCHAR(35) NULL")
        )
        changed = True
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("book_narrations")
        if constraint.get("name")
    }
    if "uq_book_voice_narration" in unique_constraints:
        if db.engine.dialect.name != "mysql":
            raise RuntimeError(
                "The legacy uq_book_voice_narration constraint must be removed "
                "before startup."
            )
        db.session.execute(
            text("ALTER TABLE book_narrations DROP INDEX uq_book_voice_narration")
        )
        changed = True
    if changed:
        db.session.commit()


def _ensure_mini_game_generation_schema():
    """Idempotently add generation lifecycle and server-grading columns."""
    inspector = inspect(db.engine)
    if inspector.has_table("mini_games"):
        columns = {column["name"] for column in inspector.get_columns("mini_games")}
        additions = {
            "generation_status": "VARCHAR(20) NULL",
            "generator_provider": "VARCHAR(50) NULL",
            "generator_model": "VARCHAR(200) NULL",
            "generator_version": "VARCHAR(50) NULL",
            "source_content_hash": "VARCHAR(64) NULL",
            "generated_at": "DATETIME NULL",
            "generation_error": "VARCHAR(500) NULL",
            "content_version": "INTEGER NULL",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.session.execute(text(f"ALTER TABLE mini_games ADD COLUMN {name} {definition}"))
        db.session.execute(text(
            "UPDATE mini_games SET generation_status = 'fallback' "
            "WHERE generation_status IS NULL"
        ))
        indexes = {index["name"] for index in inspect(db.engine).get_indexes("mini_games")}
        mini_game_indexes = {
            "ix_mini_games_book_id": "book_id",
            "ix_mini_games_generation_status": "generation_status",
            "ix_mini_games_source_content_hash": "source_content_hash",
            "uq_mini_games_book_type_version": "book_id, game_type, content_version",
        }
        for name, columns_sql in mini_game_indexes.items():
            if name not in indexes:
                unique = "UNIQUE " if name.startswith("uq_") else ""
                db.session.execute(text(
                    f"CREATE {unique}INDEX {name} ON mini_games ({columns_sql})"
                ))

    inspector = inspect(db.engine)
    if inspector.has_table("game_results"):
        columns = {column["name"] for column in inspector.get_columns("game_results")}
        additions = {
            "correct_answers": "INTEGER NULL",
            "total_questions": "INTEGER NULL",
            "answers_data": "JSON NULL",
            "game_content_version": "INTEGER NULL",
            "points_awarded": "INTEGER NULL",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.session.execute(text(f"ALTER TABLE game_results ADD COLUMN {name} {definition}"))
        db.session.execute(text(
            "UPDATE game_results SET points_awarded = score "
            "WHERE points_awarded IS NULL"
        ))
        indexes = {index["name"] for index in inspect(db.engine).get_indexes("game_results")}
        for name, column in {
            "ix_game_results_child_id": "child_id",
            "ix_game_results_game_id": "game_id",
        }.items():
            if name not in indexes:
                db.session.execute(text(f"CREATE INDEX {name} ON game_results ({column})"))
    db.session.commit()


def _ensure_profile_image_schema():
    """Add optional profile image fields to existing account and child tables."""
    for table in ("parents", "children"):
        inspector = inspect(db.engine)
        if not inspector.has_table(table):
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "profile_image_url" not in columns:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN profile_image_url VARCHAR(500) NULL"))
        if "profile_image_public_id" not in columns:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN profile_image_public_id VARCHAR(255) NULL"))
    db.session.commit()


def _prepare_teacher_application_table():
    """Rename and preserve the former teacher_profiles table before create_all."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    has_legacy = "teacher_profiles" in tables
    has_applications = "teacher_applications" in tables
    if not has_legacy:
        return

    if not has_applications:
        statement = (
            "RENAME TABLE teacher_profiles TO teacher_applications"
            if db.engine.dialect.name == "mysql"
            else "ALTER TABLE teacher_profiles RENAME TO teacher_applications"
        )
        db.session.execute(text(statement))
        db.session.commit()
        return

    # A previously interrupted deployment may have created the new table
    # before moving the old rows. Copy only accounts not already represented;
    # the legacy table remains untouched so this recovery path cannot lose data.
    _ensure_teacher_application_columns()
    legacy_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("teacher_profiles")
    }
    if "account_id" not in legacy_columns:
        raise RuntimeError(
            "teacher_profiles has no account_id column; its rows cannot be "
            "safely merged without guessing ownership."
        )
    fallback_expressions = {
        "approval_status": "'approved'",
        "created_at": "CURRENT_TIMESTAMP",
        "updated_at": "CURRENT_TIMESTAMP",
    }
    select_expressions = [
        (
            f"legacy_application.{column}"
            if column in legacy_columns
            else fallback_expressions.get(column, "NULL")
        )
        for column in TEACHER_APPLICATION_COPY_COLUMNS
    ]
    db.session.execute(text(
        "INSERT INTO teacher_applications ("
        + ", ".join(TEACHER_APPLICATION_COPY_COLUMNS)
        + ") SELECT "
        + ", ".join(select_expressions)
        + " FROM teacher_profiles legacy_application "
        "WHERE NOT EXISTS (SELECT 1 FROM teacher_applications "
        "existing_application WHERE existing_application.account_id = "
        "legacy_application.account_id)"
    ))
    db.session.commit()


def _ensure_teacher_application_schema():
    """Additively repair teacher application storage without deleting rows."""
    inspector = inspect(db.engine)
    if not inspector.has_table("teacher_applications"):
        from app.models.teacher_application_model import TeacherApplication
        TeacherApplication.__table__.create(bind=db.engine, checkfirst=True)

    _ensure_teacher_application_columns()
    dialect = db.engine.dialect.name

    # Only fill values that are absent. Existing pending/approved/rejected
    # decisions and timestamps are never overwritten.
    db.session.execute(text(
        "UPDATE teacher_applications SET approval_status = 'pending' "
        "WHERE approval_status IS NULL"
    ))
    db.session.execute(text(
        "UPDATE teacher_applications SET created_at = CURRENT_TIMESTAMP "
        "WHERE created_at IS NULL"
    ))
    db.session.execute(text(
        "UPDATE teacher_applications SET updated_at = CURRENT_TIMESTAMP "
        "WHERE updated_at IS NULL"
    ))

    null_account_count = db.session.execute(text(
        "SELECT COUNT(*) FROM teacher_applications WHERE account_id IS NULL"
    )).scalar_one()
    if null_account_count:
        raise RuntimeError(
            "teacher_applications contains rows without account_id; ownership "
            "cannot be inferred safely, so no rows were deleted."
        )
    duplicate_account = db.session.execute(text(
        "SELECT account_id FROM teacher_applications "
        "GROUP BY account_id HAVING COUNT(*) > 1 LIMIT 1"
    )).scalar()
    if duplicate_account is not None:
        raise RuntimeError(
            "teacher_applications contains duplicate rows for an account; "
            "the unique account constraint cannot be added without a manual "
            "data review. No rows were deleted."
        )

    # Allocate new identity values above the current maximum. Using account_id
    # directly could collide with an unrelated preserved id value.
    next_id = db.session.execute(text(
        "SELECT COALESCE(MAX(id), 0) FROM teacher_applications"
    )).scalar_one()
    missing_id_accounts = db.session.execute(text(
        "SELECT account_id FROM teacher_applications WHERE id IS NULL "
        "ORDER BY account_id"
    )).scalars().all()
    for account_id in missing_id_accounts:
        next_id += 1
        db.session.execute(text(
            "UPDATE teacher_applications SET id = :id "
            "WHERE account_id = :account_id AND id IS NULL"
        ), {"id": next_id, "account_id": account_id})
    null_id_count = db.session.execute(text(
        "SELECT COUNT(*) FROM teacher_applications WHERE id IS NULL"
    )).scalar_one()
    duplicate_id = db.session.execute(text(
        "SELECT id FROM teacher_applications "
        "GROUP BY id HAVING COUNT(*) > 1 LIMIT 1"
    )).scalar()
    if null_id_count or duplicate_id is not None:
        raise RuntimeError(
            "teacher_applications has invalid id values; automatic repair "
            "stopped without deleting or replacing any rows."
        )

    invalid_status = db.session.execute(text(
        "SELECT approval_status FROM teacher_applications "
        "WHERE approval_status NOT IN ('pending', 'approved', 'rejected') "
        "LIMIT 1"
    )).scalar()
    if invalid_status is not None:
        raise RuntimeError(
            "teacher_applications contains an unsupported approval_status; "
            "automatic repair stopped without changing that value."
        )

    _ensure_teacher_application_identity_key()
    _ensure_teacher_application_index(
        ("account_id",), "uq_teacher_applications_account_id", unique=True
    )
    _ensure_teacher_application_index(
        ("approval_status",), "ix_teacher_applications_approval_status"
    )
    _ensure_teacher_application_index(
        ("reviewed_by_id",), "ix_teacher_applications_reviewed_by_id"
    )

    if dialect == "sqlite":
        db.session.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_teacher_applications_assign_id "
            "AFTER INSERT ON teacher_applications FOR EACH ROW "
            "WHEN NEW.id IS NULL BEGIN UPDATE teacher_applications "
            "SET id = (SELECT COALESCE(MAX(id), 0) + 1 "
            "FROM teacher_applications WHERE rowid != NEW.rowid) "
            "WHERE rowid = NEW.rowid; END"
        ))
    elif dialect == "mysql":
        _ensure_teacher_application_mysql_constraints()

    db.session.execute(
        text(
            "INSERT INTO teacher_applications "
            "(account_id, approval_status, created_at, updated_at) "
            "SELECT p.id, 'approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM parents p LEFT JOIN teacher_applications ta ON ta.account_id = p.id "
            "WHERE p.role = 'teacher' AND ta.account_id IS NULL"
        )
    )
    if dialect == "sqlite":
        db.session.execute(text(
            "UPDATE teacher_applications SET id = rowid WHERE id IS NULL"
        ))
    db.session.commit()


def _ensure_teacher_application_columns():
    """Add every missing application column with non-destructive ALTERs."""
    inspector = inspect(db.engine)
    if not inspector.has_table("teacher_applications"):
        return
    existing_columns = {
        column["name"]
        for column in inspector.get_columns("teacher_applications")
    }
    for column, definition in TEACHER_APPLICATION_COLUMN_DEFINITIONS.items():
        if column in existing_columns:
            continue
        statement = (
            f"ALTER TABLE teacher_applications ADD COLUMN {column} {definition}"
        )
        db.session.execute(text(statement))
    db.session.commit()


def _teacher_application_keys():
    """Return indexed column tuples with their uniqueness guarantees."""
    inspector = inspect(db.engine)
    keys = []
    for index in inspector.get_indexes("teacher_applications"):
        columns = tuple(index.get("column_names") or ())
        if columns:
            keys.append((columns, bool(index.get("unique"))))
    for constraint in inspector.get_unique_constraints("teacher_applications"):
        columns = tuple(constraint.get("column_names") or ())
        if columns:
            keys.append((columns, True))
    primary_columns = tuple(
        inspector.get_pk_constraint("teacher_applications").get(
            "constrained_columns"
        ) or ()
    )
    if primary_columns:
        keys.append((primary_columns, True))
    return keys


def _teacher_application_index_name_exists(name):
    """Avoid duplicate DDL when a renamed SQLite index is not reflected."""
    if db.engine.dialect.name == "sqlite":
        return db.session.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :name"
        ), {"name": name}).scalar() is not None
    return any(
        index.get("name") == name
        for index in inspect(db.engine).get_indexes("teacher_applications")
    )


def _ensure_teacher_application_index(columns, name, *, unique=False):
    """Create one required index only when its columns are not already covered."""
    for indexed_columns, is_unique in _teacher_application_keys():
        if indexed_columns == tuple(columns) and (is_unique or not unique):
            return
    if _teacher_application_index_name_exists(name):
        return
    unique_sql = "UNIQUE " if unique else ""
    db.session.execute(text(
        f"CREATE {unique_sql}INDEX {name} ON teacher_applications "
        f"({', '.join(columns)})"
    ))
    db.session.commit()


def _ensure_teacher_application_identity_key():
    """Ensure id is a single unique key without adding a redundant index."""
    keys = _teacher_application_keys()
    if any(columns == ("id",) and unique for columns, unique in keys):
        return
    inspector = inspect(db.engine)
    primary_columns = tuple(
        inspector.get_pk_constraint("teacher_applications").get(
            "constrained_columns"
        ) or ()
    )
    if db.engine.dialect.name == "mysql" and not primary_columns:
        db.session.execute(text(
            "ALTER TABLE teacher_applications ADD PRIMARY KEY (id)"
        ))
        db.session.commit()
        return
    _ensure_teacher_application_index(
        ("id",), "uq_teacher_applications_id", unique=True
    )


def _ensure_teacher_application_mysql_constraints():
    """Enforce MySQL identity, nullability, and account foreign keys."""
    inspector = inspect(db.engine)
    id_column = next(
        column for column in inspector.get_columns("teacher_applications")
        if column["name"] == "id"
    )
    if not id_column.get("autoincrement"):
        db.session.execute(text(
            "ALTER TABLE teacher_applications MODIFY COLUMN "
            "id INTEGER NOT NULL AUTO_INCREMENT"
        ))

    db.session.execute(text(
        "ALTER TABLE teacher_applications "
        "MODIFY COLUMN account_id INTEGER NOT NULL, "
        "MODIFY COLUMN approval_status VARCHAR(20) NOT NULL DEFAULT 'pending', "
        "MODIFY COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "MODIFY COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "ON UPDATE CURRENT_TIMESTAMP"
    ))

    inspector = inspect(db.engine)
    foreign_keys = inspector.get_foreign_keys("teacher_applications")
    if not any(
        fk.get("constrained_columns") == ["account_id"]
        and fk.get("referred_table") == "parents"
        for fk in foreign_keys
    ):
        db.session.execute(text(
            "ALTER TABLE teacher_applications ADD CONSTRAINT "
            "fk_teacher_applications_account_repair FOREIGN KEY (account_id) "
            "REFERENCES parents(id) ON DELETE CASCADE"
        ))
    if not any(
        fk.get("constrained_columns") == ["reviewed_by_id"]
        and fk.get("referred_table") == "parents"
        for fk in foreign_keys
    ):
        db.session.execute(text(
            "ALTER TABLE teacher_applications ADD CONSTRAINT "
            "fk_teacher_applications_reviewer_repair FOREIGN KEY (reviewed_by_id) "
            "REFERENCES parents(id) ON DELETE SET NULL"
        ))
    db.session.commit()


def _ensure_teacher_profile_schema():
    """Compatibility wrapper for older deployment helpers and tests."""
    _prepare_teacher_application_table()
    _ensure_teacher_application_schema()
