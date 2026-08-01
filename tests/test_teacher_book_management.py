"""Authorization, attribution, media, and engagement tests for teacher books."""

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from flask_jwt_extended import create_access_token
from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.asset_model import Asset, BOOK_COVER_IMAGE, BOOK_IMAGE
from app.models.book_model import Book
from app.models.child_model import Child
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER
from app.models.reading_session_model import ReadingSession
from app.models.teacher_profile_model import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TeacherProfile,
)
from app.security import book_creation_attempts

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class TeacherBookManagementTests(unittest.TestCase):
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
        self.admin = self._account("Admin", "admin.books@example.com", ROLE_ADMIN)
        self.parent = self._account("Parent", "parent.books@example.com", ROLE_PARENT)
        self.teacher = self._teacher("Nimal Perera", "nimal@example.com", APPROVAL_APPROVED)
        self.other_teacher = self._teacher("Other Teacher", "other.teacher@example.com", APPROVAL_APPROVED)
        self.pending = self._teacher("Pending", "pending.books@example.com", APPROVAL_PENDING)
        self.rejected = self._teacher("Rejected", "rejected.books@example.com", APPROVAL_REJECTED)
        self.banned = self._teacher("Banned", "banned.books@example.com", APPROVAL_APPROVED)
        self.banned.is_banned = True
        db.session.flush()
        self.child = Child(parent_id=self.parent.id, created_by_id=self.parent.id, name="Reader", age=8)
        db.session.add(self.child)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        for account in (self.teacher, self.other_teacher, self.pending, self.rejected, self.banned):
            book_creation_attempts.reset(f"teacher-book:{account.id}")
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

    def _teacher(self, name, email, status):
        account = self._account(name, email, ROLE_TEACHER)
        db.session.flush()
        account.teacher_profile = TeacherProfile(approval_status=status)
        return account

    def _headers(self, account, key=None):
        headers = {"Authorization": f"Bearer {create_access_token(identity=account.id)}"}
        if key:
            headers["Idempotency-Key"] = key
        return headers

    @staticmethod
    def _payload(title="Teacher Story", **overrides):
        payload = {
            "title": title,
            "description": "A friendly story.",
            "age_group": "7-9",
            "reading_level": "beginner",
            "text_content": "A fox found a kind friend.",
            "image_urls": [],
        }
        payload.update(overrides)
        return payload

    def _create(self, teacher=None, title="Teacher Story", key="create-key-0001"):
        teacher = teacher or self.teacher
        return self.client.post(
            "/api/books", json=self._payload(title), headers=self._headers(teacher, key)
        )

    def test_only_approved_teachers_can_create_and_creator_cannot_be_spoofed(self):
        created = self.client.post(
            "/api/books",
            json=self._payload(created_by_account_id=self.other_teacher.id),
            headers=self._headers(self.teacher, "create-key-0002"),
        )
        self.assertEqual(created.status_code, 201, created.json)
        book = db.session.get(Book, created.json["book"]["id"])
        self.assertEqual(book.created_by_account_id, self.teacher.id)
        self.assertEqual(book.creator_name_snapshot, "Nimal Perera")
        self.assertNotIn("email", created.json["book"]["created_by"])
        self.assertEqual(created.json["book"]["created_by_label"], "Created by Nimal Perera")

        for account in (self.pending, self.rejected, self.banned, self.parent):
            response = self.client.post(
                "/api/books", json=self._payload(), headers=self._headers(account, f"deny-key-{account.id}")
            )
            self.assertEqual(response.status_code, 403, (account.email, response.json))

    def test_idempotency_key_prevents_duplicate_books(self):
        first = self._create(key="same-request-123")
        second = self._create(key="same-request-123")
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(Book.query.filter_by(created_by_account_id=self.teacher.id).count(), 1)

    def test_teacher_lists_only_owns_and_cannot_read_another_edit_record(self):
        own = self._create(key="own-list-0001").json["book"]
        other = self._create(self.other_teacher, "Other Story", "other-list-001").json["book"]
        listed = self.client.get("/api/teacher/books", headers=self._headers(self.teacher))
        self.assertEqual([item["id"] for item in listed.json["books"]], [own["id"]])
        hidden = self.client.get(f"/api/teacher/books/{other['id']}", headers=self._headers(self.teacher))
        self.assertEqual(hidden.status_code, 403)

    def test_teacher_can_update_and_delete_own_but_not_another_book(self):
        own_id = self._create(key="own-edit-0001").json["book"]["id"]
        other_id = self._create(self.other_teacher, "Other", "other-edit-001").json["book"]["id"]
        updated = self.client.patch(
            f"/api/teacher/books/{own_id}", json=self._payload("Updated Story"),
            headers=self._headers(self.teacher),
        )
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json["book"]["title"], "Updated Story")
        self.assertEqual(self.client.patch(
            f"/api/teacher/books/{other_id}", json=self._payload("Stolen"),
            headers=self._headers(self.teacher),
        ).status_code, 403)
        self.assertEqual(self.client.delete(
            f"/api/teacher/books/{other_id}", headers=self._headers(self.teacher)
        ).status_code, 403)
        self.assertEqual(self.client.delete(
            f"/api/teacher/books/{own_id}", headers=self._headers(self.teacher)
        ).status_code, 200)
        self.assertIsNone(db.session.get(Book, own_id))

    def test_admin_can_manage_teacher_books_without_changing_attribution(self):
        book_id = self._create(key="admin-manage-1").json["book"]["id"]
        updated = self.client.patch(
            f"/api/admin/books/{book_id}", json=self._payload("Admin corrected"),
            headers=self._headers(self.admin),
        )
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json["book"]["created_by_label"], "Created by Nimal Perera")
        self.assertEqual(self.client.delete(
            f"/api/admin/books/{book_id}", headers=self._headers(self.admin)
        ).status_code, 200)

    def test_legacy_fallback_and_deleted_teacher_snapshot_preserve_books(self):
        legacy = Book(title="Legacy", age_group="7-9", reading_level="beginner")
        db.session.add(legacy)
        db.session.commit()
        self.assertEqual(legacy.to_dict()["created_by_label"], "Created by TeachAlike")
        book_id = self._create(key="delete-owner-1").json["book"]["id"]
        teacher_id = self.teacher.id
        db.session.add(Asset.from_cloudinary_metadata(
            {
                "asset_id": "surviving-cover", "public_id": "server/surviving-cover",
                "secure_url": "https://res.cloudinary.test/surviving-cover.png",
                "resource_type": "image", "delivery_type": "upload", "format": "png",
                "bytes": len(PNG), "width": 1, "height": 1, "duration": None,
                "asset_folder": "server/book", "original_filename": "cover.png",
            },
            category=BOOK_COVER_IMAGE,
            owner_user_id=teacher_id,
            book_id=book_id,
        ))
        db.session.commit()
        with patch("app.controllers.admin_controller.schedule_account_asset_cleanup"):
            deleted = self.client.delete(
                f"/api/admin/teachers/{teacher_id}", headers=self._headers(self.admin)
            )
        self.assertEqual(deleted.status_code, 202, deleted.json)
        db.session.expire_all()
        book = db.session.get(Book, book_id)
        self.assertIsNotNone(book)
        self.assertIsNone(book.created_by_account_id)
        self.assertEqual(book.to_dict()["created_by_label"], "Created by Nimal Perera")
        surviving_asset = Asset.query.filter_by(book_id=book_id).one()
        self.assertIsNone(surviving_asset.owner_user_id)

    @patch("app.controllers.teacher_book_controller.upload_asset")
    def test_media_is_validated_uploaded_to_ledger_and_server_folder(self, upload):
        upload.return_value = {
            "asset_id": "asset-cover", "public_id": "server/book/cover",
            "secure_url": "https://res.cloudinary.test/cover.png", "resource_type": "image",
            "delivery_type": "upload", "format": "png", "bytes": len(PNG),
            "width": 100, "height": 100, "duration": None,
            "asset_folder": "teachalike/3/Image/Books/1_story", "original_filename": "cover.png",
        }
        response = self.client.post(
            "/api/books",
            data={
                "title": "Media Story", "age_group": "7-9", "reading_level": "beginner",
                "text_content": "Story text", "image_urls": "[]",
                "cover_image": (io.BytesIO(PNG), "cover.png", "image/png"),
            },
            content_type="multipart/form-data",
            headers=self._headers(self.teacher, "media-key-0001"),
        )
        self.assertEqual(response.status_code, 201, response.json)
        book_id = response.json["book"]["id"]
        asset = Asset.query.filter_by(book_id=book_id, asset_category=BOOK_IMAGE).one()
        self.assertEqual(asset.owner_user_id, self.teacher.id)
        call = upload.call_args
        self.assertEqual(
            call.args[1],
            f"teachalike/Books/{self.teacher.id}_nimal_perera/{book_id}_media_story/Images",
        )

    @patch("app.controllers.teacher_book_controller.cleanup_references")
    @patch("app.controllers.teacher_book_controller.upload_asset")
    def test_failed_database_save_cleans_new_cloudinary_upload(self, upload, cleanup):
        upload.return_value = {
            "asset_id": "failed-asset", "public_id": "server/failed",
            "secure_url": "https://res.cloudinary.test/failed.png", "resource_type": "image",
            "delivery_type": "upload", "format": "png", "bytes": len(PNG),
            "width": 1, "height": 1, "duration": None,
            "asset_folder": "server/folder", "original_filename": "cover.png",
        }
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("failed")):
            response = self.client.post(
                "/api/books",
                data={
                    "title": "Failure", "age_group": "7-9", "reading_level": "beginner",
                    "image_urls": "[]", "cover_image": (io.BytesIO(PNG), "cover.png", "image/png"),
                }, content_type="multipart/form-data",
                headers=self._headers(self.teacher, "failure-key-01"),
            )
        self.assertEqual(response.status_code, 500, response.json)
        cleanup.assert_called()
        self.assertIsNone(Book.query.filter_by(title="Failure").first())

    def test_teacher_book_uses_existing_views_reads_likes_and_admin_analytics(self):
        book_id = self._create(key="engage-key-001").json["book"]["id"]
        self.client.post(f"/api/books/{book_id}/views", headers=self._headers(self.parent))
        self.client.put(f"/api/books/{book_id}/likes/{self.child.id}", headers=self._headers(self.parent))
        db.session.add(ReadingSession(child_id=self.child.id, book_id=book_id))
        db.session.commit()
        mine = self.client.get("/api/teacher/books", headers=self._headers(self.teacher)).json["books"][0]
        self.assertEqual((mine["total_views"], mine["total_reads"], mine["likes"]), (1, 1, 1))
        analytics = self.client.get(
            "/api/admin/book-analytics?search=Nimal", headers=self._headers(self.admin)
        )
        self.assertEqual(analytics.status_code, 200, analytics.json)
        self.assertEqual(analytics.json["books"][0]["created_by_label"], "Created by Nimal Perera")

    def test_pending_teacher_cannot_update_books(self):
        book_id = self._create(key="pending-edit-base").json["book"]["id"]
        response = self.client.patch(
            f"/api/teacher/books/{book_id}", json=self._payload(), headers=self._headers(self.pending)
        )
        self.assertEqual(response.status_code, 403)

    def test_rejected_teacher_cannot_delete_books(self):
        book_id = self._create(key="reject-delete-base").json["book"]["id"]
        response = self.client.delete(
            f"/api/teacher/books/{book_id}", headers=self._headers(self.rejected)
        )
        self.assertEqual(response.status_code, 403)

    def test_banned_teacher_cannot_edit_or_delete_books(self):
        book_id = self._create(key="banned-actions-base").json["book"]["id"]
        patch_response = self.client.patch(
            f"/api/teacher/books/{book_id}", json=self._payload(), headers=self._headers(self.banned)
        )
        delete_response = self.client.delete(
            f"/api/teacher/books/{book_id}", headers=self._headers(self.banned)
        )
        self.assertEqual((patch_response.status_code, delete_response.status_code), (403, 403))

    def test_parent_cannot_access_teacher_book_management_list(self):
        response = self.client.get("/api/teacher/books", headers=self._headers(self.parent))
        self.assertEqual(response.status_code, 403)

    def test_missing_teacher_book_returns_not_found(self):
        response = self.client.get("/api/teacher/books/999999", headers=self._headers(self.teacher))
        self.assertEqual(response.status_code, 404)

    def test_book_attribution_never_serializes_private_teacher_fields(self):
        self.teacher.teacher_profile.phone_number = "+94 secret"
        self.teacher.teacher_profile.address = "Private home"
        self.teacher.teacher_profile.school_name = "Private school"
        db.session.commit()
        payload = self._create(key="private-fields-book").json["book"]
        serialized = str(payload)
        self.assertNotIn("+94 secret", serialized)
        self.assertNotIn("Private home", serialized)
        self.assertNotIn("Private school", serialized)
        self.assertNotIn(self.teacher.email, serialized)

    def test_admin_created_book_has_teachalike_attribution(self):
        response = self.client.post(
            "/api/admin/books", json=self._payload("Admin Story"), headers=self._headers(self.admin)
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertIsNone(response.json["book"]["created_by"])
        self.assertEqual(response.json["book"]["created_by_label"], "Created by TeachAlike")

    def test_teacher_cannot_delete_book_with_reading_sessions(self):
        book_id = self._create(key="session-delete-book").json["book"]["id"]
        db.session.add(ReadingSession(child_id=self.child.id, book_id=book_id))
        db.session.commit()
        response = self.client.delete(
            f"/api/teacher/books/{book_id}", headers=self._headers(self.teacher)
        )
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(Book, book_id))

    def test_spoofed_teacher_book_image_is_rejected_before_upload(self):
        response = self.client.post(
            "/api/books",
            data={
                "title": "Spoof", "age_group": "7-9", "reading_level": "beginner",
                "image_urls": "[]",
                "cover_image": (io.BytesIO(b"not an image"), "cover.png", "image/png"),
            }, content_type="multipart/form-data",
            headers=self._headers(self.teacher, "spoofed-image-key"),
        )
        self.assertEqual(response.status_code, 415, response.json)
        self.assertIsNone(Book.query.filter_by(title="Spoof").first())

    def test_teacher_aggregate_list_contains_no_individual_child_data(self):
        self._create(key="aggregate-safe-key")
        response = self.client.get("/api/teacher/books", headers=self._headers(self.teacher))
        self.assertEqual(response.status_code, 200, response.json)
        serialized = str(response.json)
        self.assertNotIn(self.child.name, serialized)
        self.assertNotIn("child_id", serialized)


if __name__ == "__main__":
    unittest.main()
