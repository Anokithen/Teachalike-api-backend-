"""Regression coverage for teacher approval and book engagement."""

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from flask_jwt_extended import create_access_token
from sqlalchemy.exc import SQLAlchemyError

from app import create_app, _ensure_teacher_profile_schema
from app.config import Config
from app.extensions import db
from app.models.asset_model import Asset, USER_PROFILE_IMAGE
from app.models.book_like_model import BookLike
from app.models.book_model import Book
from app.models.book_view_model import BookView
from app.models.child_model import Child
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER
from app.models.reading_session_model import ReadingSession
from app.models.teacher_profile_model import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TeacherProfile,
)
from app.security import anonymized_key, login_attempts, registration_attempts
from app.utils import utc_now


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class TeacherApprovalAndEngagementTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.original_uri = Config.SQLALCHEMY_DATABASE_URI
        self.original_options = Config.SQLALCHEMY_ENGINE_OPTIONS
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.app.config.update(TESTING=True, MAX_PROFILE_IMAGE_SIZE_MB=1)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

        self.parent = self._account("Parent", "parent.engagement@example.com", ROLE_PARENT)
        self.other = self._account("Other", "other.engagement@example.com", ROLE_PARENT)
        self.admin = self._account("Admin", "admin.engagement@example.com", ROLE_ADMIN)
        self.teacher = self._account("Teacher", "approved.teacher@example.com", ROLE_TEACHER)
        db.session.flush()
        self.teacher.teacher_profile = TeacherProfile(approval_status=APPROVAL_APPROVED)
        self.child = Child(parent_id=self.parent.id, created_by_id=self.parent.id, name="Reader", age=8)
        self.other_child = Child(parent_id=self.other.id, created_by_id=self.other.id, name="Other reader", age=9)
        self.book = Book(title="Engagement Book", age_group="7-9", reading_level="beginner")
        self.empty_book = Book(title="Empty Book", age_group="7-9", reading_level="beginner")
        db.session.add_all([self.child, self.other_child, self.book, self.empty_book])
        db.session.commit()
        self.client = self.app.test_client()
        self.remote_addr = "127.10.20.30"

    def tearDown(self):
        for email in (
            "public.parent@example.com",
            "public.teacher@example.com",
            "pending.login@example.com",
            "rejected.login@example.com",
            "admin.created@example.com",
            "legacy.teacher@example.com",
        ):
            login_attempts.reset(anonymized_key("login-account", email))
        login_attempts.reset(anonymized_key("login-ip", self.remote_addr))
        registration_attempts.reset(anonymized_key("register-ip", self.remote_addr))
        db.session.remove()
        db.drop_all()
        self.context.pop()
        Config.SQLALCHEMY_DATABASE_URI = self.original_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = self.original_options
        os.unlink(self.database_path)

    def _account(self, name, email, role):
        account = Parent(name=name, email=email, role=role)
        account.set_password("SecurePass123!")
        db.session.add(account)
        return account

    def _headers(self, account):
        return {"Authorization": f"Bearer {create_access_token(identity=account.id)}"}

    def _post(self, path, **kwargs):
        kwargs.setdefault("environ_base", {"REMOTE_ADDR": self.remote_addr})
        return self.client.post(path, **kwargs)

    @staticmethod
    def _cloudinary_metadata():
        return {
            "asset_id": "cloud-asset-1",
            "public_id": "teachalike/99/Image/Profile/profile",
            "secure_url": "https://res.cloudinary.test/profile.png",
            "resource_type": "image",
            "delivery_type": "upload",
            "format": "png",
            "bytes": len(PNG),
            "width": 100,
            "height": 100,
            "duration": None,
            "asset_folder": "teachalike/99/Image/Profile",
            "original_filename": "teacher.png",
        }

    def _teacher_form(self, **overrides):
        data = {
            "account_type": "teacher",
            "name": "Public Teacher",
            "email": "public.teacher@example.com",
            "password": "SecurePass123!",
            "phone_number": "+94 77 123 4567",
            "address": "10 Learning Road, Colombo",
            "teacher_type": "private_tuition",
            "tuition_name": "Bright Readers",
            "professional_photo": (io.BytesIO(PNG), "teacher.png", "image/png"),
        }
        data.update(overrides)
        return data

    def test_parent_registration_still_works_and_admin_type_is_rejected(self):
        parent_response = self._post("/api/auth/register", json={
            "account_type": "parent", "name": "Public Parent",
            "email": "public.parent@example.com", "password": "SecurePass123!",
        })
        self.assertEqual(parent_response.status_code, 201, parent_response.json)
        self.assertEqual(parent_response.json["parent"]["role"], ROLE_PARENT)

        admin_response = self._post("/api/auth/register", json={
            "account_type": "admin", "name": "No Admin",
            "email": "no.admin@example.com", "password": "SecurePass123!",
        })
        self.assertEqual(admin_response.status_code, 400, admin_response.json)
        self.assertIsNone(Parent.query.filter_by(email="no.admin@example.com").first())

    def test_teacher_registration_requires_fields_and_valid_image(self):
        missing = self._post("/api/auth/register", data={
            "account_type": "teacher", "name": "Teacher",
            "email": "missing@example.com", "password": "SecurePass123!",
        }, content_type="multipart/form-data")
        self.assertEqual(missing.status_code, 400, missing.json)
        self.assertIn("phone_number is required.", missing.json["errors"])
        self.assertIn("professional_photo is required.", missing.json["errors"])

        spoofed = self._post("/api/auth/register", data=self._teacher_form(
            professional_photo=(io.BytesIO(b"not an image"), "teacher.png", "image/png")
        ), content_type="multipart/form-data")
        self.assertEqual(spoofed.status_code, 415, spoofed.json)

        oversized = self._post("/api/auth/register", data=self._teacher_form(
            professional_photo=(io.BytesIO(PNG + b"x" * (1024 * 1024)), "teacher.png", "image/png")
        ), content_type="multipart/form-data")
        self.assertEqual(oversized.status_code, 413, oversized.json)

    @patch("app.controllers.auth_controller.upload_asset")
    def test_successful_teacher_application_is_pending_with_asset_ledger(self, upload_asset):
        upload_asset.return_value = self._cloudinary_metadata()
        response = self._post("/api/auth/register", data=self._teacher_form(), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 202, response.json)
        teacher = Parent.query.filter_by(email="public.teacher@example.com").one()
        self.assertEqual(teacher.role, ROLE_TEACHER)
        self.assertFalse(teacher.is_banned)
        self.assertEqual(teacher.teacher_profile.approval_status, APPROVAL_PENDING)
        self.assertEqual(teacher.profile_image_url, "https://res.cloudinary.test/profile.png")
        self.assertEqual(Asset.query.filter_by(owner_user_id=teacher.id, asset_category=USER_PROFILE_IMAGE).count(), 1)

    @patch("app.controllers.auth_controller.delete_asset")
    @patch("app.controllers.auth_controller.upload_asset")
    def test_teacher_registration_database_failure_rolls_back_and_cleans_upload(self, upload_asset, delete_asset):
        upload_asset.return_value = self._cloudinary_metadata()
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("metadata failure")):
            response = self._post(
                "/api/auth/register",
                data=self._teacher_form(email="cleanup.teacher@example.com"),
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 500, response.json)
        self.assertIsNone(Parent.query.filter_by(email="cleanup.teacher@example.com").first())
        delete_asset.assert_called_once_with(
            "teachalike/99/Image/Profile/profile", "image", "upload"
        )

    def test_pending_rejected_and_approved_login_rules(self):
        pending = self._account("Pending", "pending.login@example.com", ROLE_TEACHER)
        rejected = self._account("Rejected", "rejected.login@example.com", ROLE_TEACHER)
        db.session.flush()
        pending.teacher_profile = TeacherProfile(approval_status=APPROVAL_PENDING)
        rejected.teacher_profile = TeacherProfile(approval_status=APPROVAL_REJECTED, rejection_reason="Photo is unclear.")
        db.session.commit()

        pending_login = self._post("/api/auth/login", json={"email": pending.email, "password": "SecurePass123!"})
        self.assertEqual(pending_login.status_code, 403)
        self.assertEqual(pending_login.json["error_code"], "TEACHER_APPROVAL_PENDING")
        self.assertNotIn("access_token", pending_login.json)

        rejected_login = self._post("/api/auth/login", json={"email": rejected.email, "password": "SecurePass123!"})
        self.assertEqual(rejected_login.status_code, 403)
        self.assertEqual(rejected_login.json["error_code"], "TEACHER_APPROVAL_REJECTED")
        self.assertEqual(rejected_login.json["rejection_reason"], "Photo is unclear.")

        approved_login = self._post("/api/auth/login", json={"email": self.teacher.email, "password": "SecurePass123!"})
        self.assertEqual(approved_login.status_code, 200, approved_login.json)

    def test_status_change_blocks_existing_access_and_refresh_tokens(self):
        login = self._post("/api/auth/login", json={"email": self.teacher.email, "password": "SecurePass123!"})
        self.teacher.teacher_profile.approval_status = APPROVAL_REJECTED
        db.session.commit()
        access = self.client.get("/api/parents/me", headers={"Authorization": f"Bearer {login.json['access_token']}"})
        refresh = self._post("/api/auth/refresh", headers={"Authorization": f"Bearer {login.json['refresh_token']}"})
        self.assertEqual(access.status_code, 403)
        self.assertEqual(refresh.status_code, 403)
        self.assertEqual(access.json["error_code"], "TEACHER_APPROVAL_REJECTED")
        self.assertEqual(refresh.json["error_code"], "TEACHER_APPROVAL_REJECTED")

    @patch("app.controllers.admin_controller.upload_asset")
    def test_legacy_and_admin_created_teachers_are_approved(self, upload_asset):
        upload_asset.return_value = self._cloudinary_metadata()
        legacy = self._account("Legacy", "legacy.teacher@example.com", ROLE_TEACHER)
        db.session.commit()
        self.assertIsNone(legacy.teacher_profile)
        _ensure_teacher_profile_schema()
        db.session.expire_all()
        self.assertEqual(db.session.get(Parent, legacy.id).teacher_profile.approval_status, APPROVAL_APPROVED)

        created = self._post(
            "/api/admin/teachers",
            headers=self._headers(self.admin),
            data={
                "name": "Admin Created",
                "email": "admin.created@example.com",
                "password": "SecurePass123!",
                "phone_number": "+94 71 555 0101",
                "address": "20 School Lane, Colombo",
                "teacher_type": "school",
                "school_name": "TeachAlike Academy",
                "professional_photo": (io.BytesIO(PNG), "admin-teacher.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(created.status_code, 201, created.json)
        account = Parent.query.filter_by(email="admin.created@example.com").one()
        self.assertEqual(account.teacher_profile.approval_status, APPROVAL_APPROVED)
        self.assertEqual(account.teacher_profile.reviewed_by_id, self.admin.id)
        self.assertEqual(account.teacher_profile.phone_number, "+94 71 555 0101")
        self.assertEqual(account.teacher_profile.address, "20 School Lane, Colombo")
        self.assertEqual(account.teacher_profile.teacher_type, "school")
        self.assertEqual(account.teacher_profile.school_name, "TeachAlike Academy")
        self.assertIsNone(account.teacher_profile.tuition_name)
        self.assertEqual(account.profile_image_url, "https://res.cloudinary.test/profile.png")
        self.assertEqual(
            Asset.query.filter_by(
                owner_user_id=account.id,
                asset_category=USER_PROFILE_IMAGE,
            ).count(),
            1,
        )

    def test_teacher_admin_endpoints_are_private_and_duplicate_safe(self):
        forbidden = self.client.get("/api/admin/teachers", headers=self._headers(self.parent))
        self.assertEqual(forbidden.status_code, 403)
        approve_forbidden = self.client.patch(f"/api/admin/teachers/{self.teacher.id}/approve", headers=self._headers(self.parent))
        self.assertEqual(approve_forbidden.status_code, 403)

        first = self.client.patch(f"/api/admin/teachers/{self.teacher.id}/reject", headers=self._headers(self.admin), json={"reason": "Incomplete information"})
        second = self.client.patch(f"/api/admin/teachers/{self.teacher.id}/reject", headers=self._headers(self.admin), json={"reason": "Incomplete information"})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        details = self.client.get(f"/api/admin/teachers/{self.teacher.id}", headers=self._headers(self.admin))
        self.assertEqual(details.json["teacher"]["phone_number"], None)
        self.assertEqual(details.json["teacher"]["rejection_reason"], "Incomplete information")

        wrong_role = self.client.patch(f"/api/admin/teachers/{self.parent.id}/approve", headers=self._headers(self.admin))
        missing = self.client.patch("/api/admin/teachers/999999/approve", headers=self._headers(self.admin))
        self.assertEqual(wrong_role.status_code, 404)
        self.assertEqual(missing.status_code, 404)

    def test_private_teacher_fields_are_not_in_ordinary_serialization(self):
        self.teacher.teacher_profile.phone_number = "secret phone"
        self.teacher.teacher_profile.address = "secret address"
        db.session.commit()
        ordinary = self.teacher.to_dict()
        self.assertNotIn("phone_number", ordinary)
        self.assertNotIn("address", ordinary)
        own = self.client.get("/api/parents/me", headers=self._headers(self.teacher))
        self.assertEqual(own.json["parent"]["teacher_profile"]["phone_number"], "secret phone")

    def test_views_deduplicate_daily_and_ignore_admin(self):
        first = self._post(f"/api/books/{self.book.id}/views", headers=self._headers(self.parent))
        duplicate = self._post(f"/api/books/{self.book.id}/views", headers=self._headers(self.parent))
        admin = self._post(f"/api/books/{self.book.id}/views", headers=self._headers(self.admin))
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json["recorded"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json["recorded"])
        self.assertFalse(admin.json["recorded"])
        self.assertEqual(BookView.query.filter_by(book_id=self.book.id).count(), 1)

    def test_likes_are_unique_idempotent_authorized_and_removable(self):
        path = f"/api/books/{self.book.id}/likes/{self.child.id}"
        first = self.client.put(path, headers=self._headers(self.parent))
        duplicate = self.client.put(path, headers=self._headers(self.parent))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(BookLike.query.filter_by(book_id=self.book.id, child_id=self.child.id).count(), 1)

        unauthorized = self.client.put(path, headers=self._headers(self.other))
        self.assertEqual(unauthorized.status_code, 403)
        removed = self.client.delete(path, headers=self._headers(self.parent))
        removed_again = self.client.delete(path, headers=self._headers(self.parent))
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed_again.status_code, 200)
        self.assertEqual(BookLike.query.filter_by(book_id=self.book.id, child_id=self.child.id).count(), 0)

    def test_engagement_reads_completed_and_unique_readers_match_sessions(self):
        db.session.add_all([
            ReadingSession(child_id=self.child.id, book_id=self.book.id, completed_at=utc_now()),
            ReadingSession(child_id=self.child.id, book_id=self.book.id),
            ReadingSession(child_id=self.other_child.id, book_id=self.book.id, completed_at=utc_now()),
        ])
        db.session.commit()
        response = self.client.get(f"/api/books/{self.book.id}/engagement?child_id={self.child.id}", headers=self._headers(self.parent))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["total_reads"], 3)
        self.assertEqual(response.json["completed_reads"], 2)
        self.assertEqual(response.json["unique_readers"], 2)
        self.assertFalse(response.json["liked_by_child"])

        private = self.client.get(f"/api/books/{self.book.id}/engagement?child_id={self.child.id}", headers=self._headers(self.other))
        self.assertEqual(private.status_code, 403)

    def test_admin_analytics_zero_counts_and_rejects_non_admin(self):
        forbidden = self.client.get("/api/admin/book-analytics", headers=self._headers(self.parent))
        self.assertEqual(forbidden.status_code, 403)
        response = self.client.get("/api/admin/book-analytics?search=Empty", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json["books"]), 1)
        row = response.json["books"][0]
        for field in ("total_views", "unique_viewers", "total_reads", "completed_reads", "unique_readers", "likes"):
            self.assertEqual(row[field], 0)


if __name__ == "__main__":
    unittest.main()
