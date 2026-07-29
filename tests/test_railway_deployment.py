"""Railway configuration and readiness regression tests."""

import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import text

from app import create_app
from app.config import Config, _build_database_uri, _is_railway_environment
from app.extensions import db


class RailwayConfigTests(unittest.TestCase):
    def test_current_railway_environment_marker_is_detected(self):
        with patch.dict(
            os.environ,
            {"RAILWAY_ENVIRONMENT_ID": "environment-id"},
            clear=True,
        ):
            self.assertTrue(_is_railway_environment())

    def test_private_database_url_is_preferred_to_public_url(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "mysql://private-user:secret@mysql.railway.internal:3306/app",
                "MYSQL_PUBLIC_URL": "mysql://public-user:secret@proxy.example:1234/app",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://private-user:secret@mysql.railway.internal:3306/app",
        )

    def test_mysql_url_is_converted_to_the_pymysql_driver(self):
        with patch.dict(
            os.environ,
            {
                "MYSQL_URL": "mysql://railway-user:secret@mysql.railway.internal:3306/railway",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://railway-user:secret@mysql.railway.internal:3306/railway",
        )

    def test_individual_railway_mysql_variables_are_supported(self):
        with patch.dict(
            os.environ,
            {
                "MYSQLUSER": "railway-user",
                "MYSQLPASSWORD": "p@ss/word",
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://railway-user:p%40ss%2Fword"
            "@mysql.railway.internal:3306/railway",
        )

    def test_malformed_mysql_url_does_not_block_db_variable_fallback(self):
        with patch.dict(
            os.environ,
            {
                "MYSQL_URL": "proxy.example:12491",
                "DB_USER": "root",
                "DB_PASSWORD": "secret",
                "DB_HOST": "working.proxy.example",
                "DB_PORT": "12491",
                "DB_NAME": "railway",
            },
            clear=True,
        ):
            uri = _build_database_uri()

        self.assertEqual(
            uri,
            "mysql+pymysql://root:secret"
            "@working.proxy.example:12491/railway",
        )


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        os.unlink(self.database_path)

    def test_liveness_and_database_readiness_are_separate(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["database"], "ready")

    def test_readiness_fails_when_a_required_table_is_missing(self):
        db.session.execute(text("DROP TABLE revoked_tokens"))
        db.session.commit()

        self.assertEqual(self.client.get("/health").status_code, 200)
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["database"], "unavailable")


if __name__ == "__main__":
    unittest.main()
