"""Security and authorization regression tests."""

import json
import os
import tempfile
import unittest
from itertools import count

from flask_jwt_extended import create_access_token

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.book_model import Book
from app.models.child_model import Child
from app.models.mini_game_model import MiniGame
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER
from app.security import (
    account_password_attempts,
    anonymized_key,
    login_attempts,
    pin_attempts,
    registration_attempts,
)


REMOTE_ADDRESSES = count(10)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            LOGIN_RATE_LIMIT_ATTEMPTS=3,
            LOGIN_RATE_LIMIT_WINDOW_SECONDS=60,
            REGISTER_RATE_LIMIT_ATTEMPTS=20,
            REGISTER_RATE_LIMIT_WINDOW_SECONDS=60,
            PIN_RATE_LIMIT_ATTEMPTS=2,
            PIN_RATE_LIMIT_WINDOW_SECONDS=60,
            ACCOUNT_PASSWORD_RATE_LIMIT_ATTEMPTS=2,
            ACCOUNT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS=60,
        )
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

        self.parent = Parent(
            name="Parent",
            email="parent@example.com",
            role=ROLE_PARENT,
        )
        self.parent.set_password("SecurePass123!")
        self.other = Parent(
            name="Other",
            email="other@example.com",
            role=ROLE_PARENT,
        )
        self.other.set_password("SecurePass123!")
        self.admin = Parent(
            name="Admin",
            email="admin@example.com",
            role=ROLE_ADMIN,
        )
        self.admin.set_password("SecurePass123!")
        db.session.add_all([self.parent, self.other, self.admin])
        db.session.commit()
        self.client = self.app.test_client()
        self.remote_addr = f"127.0.0.{next(REMOTE_ADDRESSES)}"

    def tearDown(self):
        for email in (
            self.parent.email,
            self.other.email,
            self.admin.email,
            "new@example.com",
        ):
            login_attempts.reset(anonymized_key("login-account", email))
        login_attempts.reset(anonymized_key("login-ip", self.remote_addr))
        registration_attempts.reset(
            anonymized_key("register-ip", self.remote_addr)
        )
        account_password_attempts.reset(
            f"account-password:profile-update:{self.parent.id}"
        )
        account_password_attempts.reset(
            f"account-password:account-delete:{self.parent.id}"
        )
        db.session.remove()
        db.drop_all()
        self.context.pop()
        os.unlink(self.database_path)

    def _headers(self, account):
        return {
            "Authorization": f"Bearer {create_access_token(identity=account.id)}"
        }

    def _post(self, path, **kwargs):
        kwargs.setdefault("environ_base", {"REMOTE_ADDR": self.remote_addr})
        return self.client.post(path, **kwargs)

    def test_security_headers_and_private_cache_policy(self):
        response = self.client.get(
            "/api/parents/me",
            headers=self._headers(self.parent),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

    def test_registration_rejects_weak_or_oversized_input_and_ignores_role(self):
        weak = self._post(
            "/api/auth/register",
            json={
                "name": "New",
                "email": "new@example.com",
                "password": "short",
            },
        )
        self.assertEqual(weak.status_code, 400)

        oversized = self._post(
            "/api/auth/register",
            json={
                "name": "x" * 121,
                "email": "new@example.com",
                "password": "SecurePass123!",
            },
        )
        self.assertEqual(oversized.status_code, 400)

        response = self._post(
            "/api/auth/register",
            json={
                "name": "New",
                "email": "new@example.com",
                "password": "SecurePass123!",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["parent"]["role"], ROLE_PARENT)
