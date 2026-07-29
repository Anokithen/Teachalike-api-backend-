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

    def test_login_rate_limit_blocks_brute_force(self):
        for _ in range(3):
            response = self._post(
                "/api/auth/login",
                json={"email": self.parent.email, "password": "WrongPass123!"},
            )
            self.assertEqual(response.status_code, 401)

        blocked = self._post(
            "/api/auth/login",
            json={"email": self.parent.email, "password": "SecurePass123!"},
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_registration_rate_limit_blocks_account_creation_abuse(self):
        self.app.config["REGISTER_RATE_LIMIT_ATTEMPTS"] = 2
        for index in range(2):
            response = self._post(
                "/api/auth/register",
                json={
                    "name": f"New {index}",
                    "email": f"new{index}@example.com",
                    "password": "SecurePass123!",
                },
            )
            self.assertEqual(response.status_code, 201, response.json)

        blocked = self._post(
            "/api/auth/register",
            json={
                "name": "Blocked",
                "email": "blocked@example.com",
                "password": "SecurePass123!",
            },
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_logout_revokes_access_and_refresh_tokens(self):
        login = self._post(
            "/api/auth/login",
            json={"email": self.parent.email, "password": "SecurePass123!"},
        )
        self.assertEqual(login.status_code, 200, login.json)
        access_token = login.json["access_token"]
        refresh_token = login.json["refresh_token"]

        logout = self._post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"refresh_token": refresh_token},
        )
        self.assertEqual(logout.status_code, 200, logout.json)

        access_response = self.client.get(
            "/api/parents/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(access_response.status_code, 401)
        refresh_response = self._post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_sensitive_profile_changes_require_current_password(self):
        headers = self._headers(self.parent)

        missing = self.client.patch(
            "/api/parents/me",
            headers=headers,
            json={"password": "ChangedPass456!"},
        )
        self.assertEqual(missing.status_code, 400, missing.json)
        self.assertTrue(self.parent.check_password("SecurePass123!"))

        wrong = self.client.patch(
            "/api/parents/me",
            headers=headers,
            json={
                "current_password": "WrongPass123!",
                "email": "changed@example.com",
            },
        )
        self.assertEqual(wrong.status_code, 401, wrong.json)
        self.assertEqual(self.parent.email, "parent@example.com")

        changed = self.client.patch(
            "/api/parents/me",
            headers=headers,
            json={
                "current_password": "SecurePass123!",
                "email": "changed@example.com",
                "password": "ChangedPass456!",
            },
        )
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.parent.email, "changed@example.com")
        self.assertTrue(self.parent.check_password("ChangedPass456!"))

        name_only = self.client.patch(
            "/api/parents/me",
            headers=headers,
            json={"name": "Updated Parent"},
        )
        self.assertEqual(name_only.status_code, 200, name_only.json)
        self.assertEqual(self.parent.name, "Updated Parent")

    def test_account_deletion_requires_current_password(self):
        headers = self._headers(self.parent)

        missing = self.client.delete("/api/parents/me", headers=headers)
        self.assertEqual(missing.status_code, 400, missing.json)
        self.assertIsNotNone(db.session.get(Parent, self.parent.id))

        wrong = self.client.delete(
            "/api/parents/me",
            headers=headers,
            json={"current_password": "WrongPass123!"},
        )
        self.assertEqual(wrong.status_code, 401, wrong.json)
        self.assertIsNotNone(db.session.get(Parent, self.parent.id))

        deleted = self.client.delete(
            "/api/parents/me",
            headers=headers,
            json={"current_password": "SecurePass123!"},
        )
        self.assertEqual(deleted.status_code, 202, deleted.json)
        self.assertIsNone(db.session.get(Parent, self.parent.id))

    def test_pin_rate_limit_blocks_brute_force(self):
        child = Child(
            parent_id=self.parent.id,
            created_by_id=self.parent.id,
            name="Child",
            age=8,
        )
        child.set_pin("123456")
        db.session.add(child)
        db.session.commit()
        headers = self._headers(self.parent)
        key = f"pin:{self.parent.id}:{child.id}"
        try:
            for _ in range(2):
                response = self._post(
                    f"/api/children/{child.id}/verify-pin",
                    headers=headers,
                    json={"pin": "000000"},
                )
                self.assertEqual(response.status_code, 401)
            blocked = self._post(
                f"/api/children/{child.id}/verify-pin",
                headers=headers,
                json={"pin": "123456"},
            )
            self.assertEqual(blocked.status_code, 429)
        finally:
            pin_attempts.reset(key)

    def test_cross_account_and_admin_routes_are_isolated(self):
        child = Child(
            parent_id=self.parent.id,
            created_by_id=self.parent.id,
            name="Private Child",
            age=8,
        )
        db.session.add(child)
        db.session.commit()

        hidden = self.client.get(
            f"/api/children/{child.id}",
            headers=self._headers(self.other),
        )
        self.assertEqual(hidden.status_code, 404)

        forbidden = self.client.get(
            "/api/admin/parents",
            headers=self._headers(self.parent),
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_malformed_teacher_parent_id_is_rejected(self):
        teacher = Parent(
            name="Teacher",
            email="teacher@example.com",
            role=ROLE_TEACHER,
        )
        teacher.set_password("SecurePass123!")
        db.session.add(teacher)
        db.session.commit()

        response = self._post(
            "/api/children",
            headers=self._headers(teacher),
            json={
                "name": "Child",
                "age": 8,
                "parent_id": {},
            },
        )
        self.assertEqual(response.status_code, 400, response.json)

    def test_invalid_and_oversized_json_are_rejected(self):
        invalid_shape = self._post(
            "/api/auth/register",
            data="[]",
            content_type="application/json",
        )
        self.assertEqual(invalid_shape.status_code, 400)

        previous_limit = self.app.config["MAX_JSON_BODY_SIZE_BYTES"]
        self.app.config["MAX_JSON_BODY_SIZE_BYTES"] = 20
        try:
            oversized = self._post(
                "/api/auth/register",
                data=json.dumps({"name": "x" * 30}),
                content_type="application/json",
            )
            self.assertEqual(oversized.status_code, 413)
        finally:
            self.app.config["MAX_JSON_BODY_SIZE_BYTES"] = previous_limit

    def test_admin_book_urls_reject_unsafe_schemes(self):
        for unsafe_url in ("javascript:alert(1)", "http://evil.example/cover.jpg"):
            with self.subTest(unsafe_url=unsafe_url):
                response = self._post(
                    "/api/admin/books",
                    headers=self._headers(self.admin),
                    json={
                        "title": "Unsafe",
                        "age_group": "7-9",
                        "reading_level": "beginner",
                        "cover_image_url": unsafe_url,
                    },
                )
                self.assertEqual(response.status_code, 400)

    def test_custom_games_cannot_submit_unbounded_scores(self):
        child = Child(
            parent_id=self.parent.id,
            created_by_id=self.parent.id,
            name="Player",
            age=8,
        )
        book = Book(title="Book", age_group="7-9", reading_level="beginner")
        db.session.add_all([child, book])
        db.session.flush()
        game = MiniGame(
            book_id=book.id,
            game_type="custom",
            difficulty="easy",
            content={},
        )
        db.session.add(game)
        db.session.commit()

        response = self._post(
            f"/api/mini-games/{game.id}/results",
            headers=self._headers(self.parent),
            json={"child_id": child.id, "score": 1000000},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
