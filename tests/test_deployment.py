"""Production configuration and database readiness regression tests."""

import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import text

from app import _validate_deployment_config, create_app
from app.config import (
    Config,
    _build_database_uri,
    _database_is_configured,
    _is_railway_environment,
)
from app.extensions import db


class DatabaseConfigTests(unittest.TestCase):
    def test_current_railway_marker_is_detected(self):
        with patch.dict(
            os.environ,
            {"RAILWAY_ENVIRONMENT_ID": "environment-id"},
            clear=True,
        ):
            self.assertTrue(_is_railway_environment())

    def test_database_uri_uses_only_supported_variables(self):
        with patch.dict(
            os.environ,
            {
                "DB_NAME": "railway",
                "DN_HOST": "mysql.railway.internal",
                "DB_PASSWORD": "secret",
                "DB_PORT": "3306",
                "DB_USER": "user",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://user:secret@mysql.railway.internal:3306/railway",
        )

    def test_database_credentials_are_url_encoded(self):
        with patch.dict(
            os.environ,
            {
                "DB_NAME": "teach alike",
                "DN_HOST": "database.example",
                "DB_PASSWORD": "p@ss/word",
                "DB_PORT": "3306",
                "DB_USER": "root user",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://root+user:p%40ss%2Fword"
            "@database.example:3306/teach+alike",
        )

    def test_other_database_variables_are_ignored(self):
        with patch.dict(
            os.environ,
            {
                "MYSQL_URL": "mysql://wrong:wrong@wrong:9999/wrong",
                "DATABASE_URL": "mysql://wrong:wrong@wrong:9999/wrong",
                "MYSQLHOST": "wrong",
                "DB_HOST": "wrong",
                "DB_NAME": "railway",
                "DN_HOST": "database.example",
                "DB_PASSWORD": "secret",
                "DB_PORT": "3306",
                "DB_USER": "root",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://root:secret@database.example:3306/railway",
        )

    def test_all_five_database_variables_are_required_for_deployment(self):
        complete_environment = {
            "DB_NAME": "railway",
            "DN_HOST": "database.example",
            "DB_PASSWORD": "secret",
            "DB_PORT": "3306",
            "DB_USER": "root",
        }
        with patch.dict(os.environ, complete_environment, clear=True):
            self.assertTrue(_database_is_configured())

        for missing_name in complete_environment:
            incomplete_environment = complete_environment.copy()
            incomplete_environment.pop(missing_name)
            with self.subTest(missing_name=missing_name):
                with patch.dict(os.environ, incomplete_environment, clear=True):
                    self.assertFalse(_database_is_configured())


class DeploymentValidationTests(unittest.TestCase):
    def _app_with_valid_config(self):
        app = Flask(__name__)
        app.config.update(
            IS_RAILWAY=True,
            DATABASE_IS_CONFIGURED=True,
            JWT_SECRET_KEY_IS_EPHEMERAL=False,
            JWT_SECRET_KEY="a" * 64,
            FRONTEND_ORIGINS=["https://frontend.example"],
        )
        return app

    def test_valid_deployment_config_is_accepted(self):
        _validate_deployment_config(self._app_with_valid_config())

    def test_example_jwt_secret_is_rejected(self):
        app = self._app_with_valid_config()
        app.config["JWT_SECRET_KEY"] = "replace-with-a-long-random-value"

        with self.assertRaisesRegex(RuntimeError, "not an example placeholder"):
            _validate_deployment_config(app)

    def test_wildcard_frontend_origin_is_rejected(self):
        app = self._app_with_valid_config()
        app.config["FRONTEND_ORIGINS"] = ["*"]

        with self.assertRaisesRegex(RuntimeError, "FRONTEND_ORIGINS"):
            _validate_deployment_config(app)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.original_uri = Config.SQLALCHEMY_DATABASE_URI
        self.original_options = Config.SQLALCHEMY_ENGINE_OPTIONS
        self.original_origins = Config.FRONTEND_ORIGINS
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        Config.FRONTEND_ORIGINS = ["https://frontend.example"]
        self.app = create_app()
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        Config.SQLALCHEMY_DATABASE_URI = self.original_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = self.original_options
        Config.FRONTEND_ORIGINS = self.original_origins
        os.unlink(self.database_path)

    def test_database_readiness_is_reported(self):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["database"], "ready")

    def test_readiness_rejects_an_incomplete_schema(self):
        db.session.execute(text("DROP TABLE revoked_tokens"))
        db.session.commit()

        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["database"], "unavailable")

    def test_configured_frontend_origin_is_allowed_by_cors(self):
        response = self.client.options(
            "/api/auth/login",
            headers={
                "Origin": "https://frontend.example",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://frontend.example",
        )

    def test_unconfigured_frontend_origin_is_not_allowed_by_cors(self):
        response = self.client.options(
            "/api/auth/login",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()
