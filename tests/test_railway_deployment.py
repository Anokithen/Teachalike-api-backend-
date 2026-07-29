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
