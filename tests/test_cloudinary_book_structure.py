"""Cloudinary book-root, authorization, compatibility, and cleanup coverage."""

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
from app.models.asset_model import (
    Asset,
    BOOK_IMAGE,
    BOOK_VIDEO,
    STATUS_DELETED,
    TEACHER_BOOK_AUDIO,
)
from app.models.book_model import Book
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER
from app.models.teacher_application_model import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    TeacherApplication,
)
from app.services.book_management_service import ensure_book_asset_root
from app.services.cloudinary_path_service import (
    get_book_asset_root_folder,
    get_book_images_folder,
    get_book_video_folder,
    get_teacher_book_audio_folder,
)
from app.services.cloudinary_service import CloudinaryServiceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
MP4 = b"\0\0\0\x18ftypisom" + b"\0" * 64
MP3 = b"ID3" + b"\0" * 64


class CloudinaryBookStructureTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.original_uri = Config.SQLALCHEMY_DATABASE_URI
        self.original_options = Config.SQLALCHEMY_ENGINE_OPTIONS
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            MAX_PROFILE_IMAGE_SIZE_MB=1,
            MAX_BOOK_AUDIO_SIZE_MB=1,
            MAX_BOOK_VIDEO_SIZE_MB=1,
        )
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.admin = self._account("Admin", "cloud.admin@example.com", ROLE_ADMIN)
        self.parent = self._account("Parent", "cloud.parent@example.com", ROLE_PARENT)
        self.teacher = self._teacher("Nimal Perera", "cloud.teacher@example.com", APPROVAL_APPROVED)
        self.other = self._teacher("Nimal Perera", "cloud.other@example.com", APPROVAL_APPROVED)
        self.pending = self._teacher("Pending", "cloud.pending@example.com", APPROVAL_PENDING)
        self.rejected = self._teacher("Rejected", "cloud.rejected@example.com", APPROVAL_REJECTED)
        self.banned = self._teacher("Banned", "cloud.banned@example.com", APPROVAL_APPROVED)
        self.banned.is_banned = True
        db.session.flush()
        self.book = Book(
            title="The Magic Forest", age_group="6-8", reading_level="beginner",
            created_by_account_id=self.teacher.id,
            creator_name_snapshot=self.teacher.name,
        )
        self.other_book = Book(
            title="The Magic Forest", age_group="6-8", reading_level="beginner",
            created_by_account_id=self.other.id,
            creator_name_snapshot=self.other.name,
        )
        self.admin_book = Book(title="System Story", age_group="6-8", reading_level="beginner")
        db.session.add_all([self.book, self.other_book, self.admin_book])
        db.session.commit()
        self.client = self.app.test_client()
        self.upload_number = 0

    def tearDown(self):
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
        account.teacher_application = TeacherApplication(approval_status=status)
        return account

    def _headers(self, account):
        return {"Authorization": f"Bearer {create_access_token(identity=account.id)}"}

    def _upload_result(self, file, folder, **kwargs):
        self.upload_number += 1
        resource_type = kwargs.get("resource_type", "image")
        return {
            "asset_id": f"asset-{self.upload_number}",
            "public_id": kwargs.get("public_id", f"{folder}/generated"),
            "secure_url": f"https://res.cloudinary.test/{self.upload_number}",
            "resource_type": resource_type,
            "delivery_type": kwargs.get("delivery_type", "upload"),
            "format": "mp3" if resource_type == "video" else "png",
            "bytes": 100, "width": 100 if resource_type == "image" else None,
            "height": 100 if resource_type == "image" else None,
            "duration": 1.5 if resource_type == "video" else None,
            "asset_folder": folder, "original_filename": file.filename,
        }

    def _image(self, book=None, account=None, **extra):
        book = book or self.book
        account = account or self.teacher
        data = {
            "file": (io.BytesIO(PNG), "ignored-name.png", "image/png"),
            "image_kind": "cover",
            **extra,
        }
        return self.client.post(
            f"/api/books/{book.id}/images", headers=self._headers(account),
            data=data, content_type="multipart/form-data",
        )

    @patch("app.controllers.asset_controller.upload_asset")
    def test_teacher_images_use_central_images_folder(self, upload):
        upload.side_effect = self._upload_result
        response = self._image()
        self.assertEqual(response.status_code, 201, response.json)
        expected = f"teachalike/Books/{self.teacher.id}_nimal_perera/{self.book.id}_the_magic_forest/Images"
        self.assertEqual(upload.call_args.args[1], expected)
        self.assertEqual(upload.call_args.kwargs["public_id"], f"{expected}/cover")

    @patch("app.controllers.asset_controller.upload_asset")
    def test_teacher_video_uses_central_video_folder(self, upload):
        upload.side_effect = self._upload_result
        response = self.client.post(
            f"/api/books/{self.book.id}/video", headers=self._headers(self.teacher),
            data={"file": (io.BytesIO(MP4), "movie.mp4", "video/mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.json)
        folder = f"teachalike/Books/{self.teacher.id}_nimal_perera/{self.book.id}_the_magic_forest/Video"
        self.assertEqual(upload.call_args.args[1], folder)
        self.assertEqual(upload.call_args.kwargs["public_id"], f"{folder}/video_01")

    @patch("app.controllers.asset_controller.upload_asset")
    def test_teacher_audio_uses_teacher_voice_audio_folder(self, upload):
        upload.side_effect = self._upload_result
        response = self.client.post(
            f"/api/books/{self.book.id}/teacher-audio", headers=self._headers(self.teacher),
            data={"file": (io.BytesIO(MP3), "voice.mp3", "audio/mpeg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.json)
        folder = f"teachalike/Books/{self.teacher.id}_nimal_perera/{self.book.id}_the_magic_forest/Teacher_voice_audio"
        self.assertEqual(upload.call_args.args[1], folder)
        self.assertEqual(upload.call_args.kwargs["public_id"], f"{folder}/voice_audio_teacher")
        self.assertEqual(upload.call_args.kwargs["delivery_type"], "authenticated")

    @patch("app.controllers.asset_controller.upload_asset")
    def test_admin_created_books_use_teachalike_fallback(self, upload):
        upload.side_effect = self._upload_result
        response = self.client.post(
            f"/api/admin/books/{self.admin_book.id}/images", headers=self._headers(self.admin),
            data={"file": (io.BytesIO(PNG), "cover.png", "image/png"), "image_kind": "cover"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(
            upload.call_args.args[1],
            f"teachalike/Books/TeachAlike/{self.admin_book.id}_system_story/Images",
        )

    def test_duplicate_teacher_names_have_distinct_roots(self):
        self.assertNotEqual(
            get_book_asset_root_folder(self.teacher.id, self.teacher.name, 10, "Same"),
            get_book_asset_root_folder(self.other.id, self.other.name, 10, "Same"),
        )

    def test_duplicate_book_names_have_distinct_roots(self):
        self.assertNotEqual(
            get_book_asset_root_folder(self.teacher.id, self.teacher.name, 10, "Same"),
            get_book_asset_root_folder(self.teacher.id, self.teacher.name, 11, "Same"),
        )

    def test_unsafe_names_are_sanitized_in_every_folder(self):
        root = get_book_asset_root_folder(7, "../../Nimal\\ Perera", 9, "../Magic / Forest")
        self.assertEqual(root, "teachalike/Books/7_nimal_perera/9_magic_forest")
        self.assertEqual(get_book_images_folder(7, "Nimal", 9, "Book").split("/")[-1], "Images")
        self.assertEqual(get_book_video_folder(7, "Nimal", 9, "Book").split("/")[-1], "Video")
        self.assertEqual(get_teacher_book_audio_folder(7, "Nimal", 9, "Book").split("/")[-1], "Teacher_voice_audio")

    def test_teacher_cannot_upload_to_another_teachers_book(self):
        response = self._image(book=self.other_book, account=self.teacher)
        self.assertEqual(response.status_code, 403)

    def test_nonapproved_and_banned_teachers_cannot_upload(self):
        for account in (self.pending, self.rejected, self.banned):
            response = self._image(account=account)
            self.assertEqual(response.status_code, 403, (account.email, response.json))

    def test_parent_and_child_profiles_have_no_catalog_upload_permission(self):
        response = self._image(account=self.parent)
        self.assertEqual(response.status_code, 403)

    @patch("app.controllers.asset_controller.upload_asset")
    def test_admin_can_manage_teacher_owned_assets(self, upload):
        upload.side_effect = self._upload_result
        response = self.client.post(
            f"/api/admin/books/{self.book.id}/images", headers=self._headers(self.admin),
            data={"file": (io.BytesIO(PNG), "cover.png", "image/png"), "image_kind": "cover"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.json)
        asset = Asset.query.filter_by(book_id=self.book.id, asset_category=BOOK_IMAGE).one()
        self.assertEqual(asset.owner_user_id, self.teacher.id)
        self.assertEqual(asset.admin_id, self.admin.id)

    @patch("app.controllers.asset_controller.upload_asset")
    def test_spoofed_identity_and_folder_fields_are_ignored(self, upload):
        upload.side_effect = self._upload_result
        response = self._image(
            teacher_id=self.other.id, owner_user_id=self.other.id,
            folder="../../evil", public_id="attacker/chosen",
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertIn(f"/{self.teacher.id}_nimal_perera/", upload.call_args.args[1])
        self.assertNotIn("evil", str(upload.call_args))
        self.assertNotIn("attacker", str(upload.call_args))

    def test_invalid_and_oversized_book_files_are_rejected(self):
        invalid = self.client.post(
            f"/api/books/{self.book.id}/images", headers=self._headers(self.teacher),
            data={"file": (io.BytesIO(b"fake"), "cover.png", "image/png"), "image_kind": "cover"},
            content_type="multipart/form-data",
        )
        oversized = self.client.post(
            f"/api/books/{self.book.id}/images", headers=self._headers(self.teacher),
            data={"file": (io.BytesIO(PNG + b"x" * 1024 * 1024), "cover.png", "image/png"), "image_kind": "cover"},
            content_type="multipart/form-data",
        )
        self.assertEqual((invalid.status_code, oversized.status_code), (415, 413))

    @patch("app.controllers.asset_controller.delete_asset")
    @patch("app.controllers.asset_controller.upload_asset")
    def test_database_failure_cleans_new_upload(self, upload, delete):
        upload.side_effect = self._upload_result
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("failed")):
            response = self._image()
        self.assertEqual(response.status_code, 500, response.json)
        delete.assert_called_once()

    def test_existing_cloudinary_urls_remain_unchanged(self):
        self.book.cover_image_url = "https://legacy.cloudinary.test/old-cover.png"
        self.book.asset_root_folder = None
        db.session.commit()
        ensure_book_asset_root(self.book)
        db.session.commit()
        response = self.client.get(f"/api/books/{self.book.id}", headers=self._headers(self.parent))
        self.assertEqual(response.json["book"]["cover_image_url"], "https://legacy.cloudinary.test/old-cover.png")

    @patch("app.services.book_management_service.delete_asset")
    def test_book_deletion_confirms_registered_asset_cleanup(self, delete):
        asset = Asset.from_cloudinary_metadata(
            self._upload_result(type("Upload", (), {"filename": "cover.png"})(), "saved/folder", public_id="saved/exact"),
            category=BOOK_IMAGE, owner_user_id=self.teacher.id, book_id=self.book.id,
            active_slot=f"book:{self.book.id}:cover",
        )
        db.session.add(asset)
        db.session.commit()
        response = self.client.delete(f"/api/teacher/books/{self.book.id}", headers=self._headers(self.teacher))
        self.assertEqual(response.status_code, 200, response.json)
        delete.assert_called_once_with("saved/exact", "image", "upload")
        self.assertEqual(asset.status, STATUS_DELETED)

    @patch("app.services.book_management_service.delete_asset")
    def test_book_cleanup_failure_is_retryable_and_preserves_book(self, delete):
        delete.side_effect = CloudinaryServiceError("provider unavailable")
        asset = Asset.from_cloudinary_metadata(
            self._upload_result(type("Upload", (), {"filename": "cover.png"})(), "saved/folder", public_id="saved/retry"),
            category=BOOK_IMAGE, owner_user_id=self.teacher.id, book_id=self.book.id,
            active_slot=f"book:{self.book.id}:cover",
        )
        db.session.add(asset)
        db.session.commit()
        response = self.client.delete(f"/api/teacher/books/{self.book.id}", headers=self._headers(self.teacher))
        self.assertEqual(response.status_code, 503, response.json)
        self.assertIsNotNone(db.session.get(Book, self.book.id))
        self.assertEqual(db.session.get(Asset, asset.id).status, "cleanup_failed")

    @patch("app.controllers.admin_controller.schedule_account_asset_cleanup")
    def test_deleting_teacher_preserves_book_and_book_assets(self, _schedule):
        asset = Asset.from_cloudinary_metadata(
            self._upload_result(type("Upload", (), {"filename": "cover.png"})(), "saved/folder", public_id="saved/cover"),
            category=BOOK_IMAGE, owner_user_id=self.teacher.id, book_id=self.book.id,
        )
        db.session.add(asset)
        db.session.commit()
        response = self.client.delete(f"/api/admin/teachers/{self.teacher.id}", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 202, response.json)
        self.assertIsNotNone(db.session.get(Book, self.book.id))
        self.assertIsNone(db.session.get(Asset, asset.id).owner_user_id)

    @patch("app.controllers.asset_controller.upload_asset")
    def test_teacher_and_book_renames_keep_saved_canonical_root(self, upload):
        upload.side_effect = self._upload_result
        first = self._image()
        self.assertEqual(first.status_code, 201, first.json)
        original_root = self.book.asset_root_folder
        self.teacher.name = "Renamed Teacher"
        self.book.title = "Renamed Book"
        db.session.commit()
        second = self._image(
            image_kind="picture", position="1",
        )
        self.assertEqual(second.status_code, 201, second.json)
        self.assertEqual(self.book.asset_root_folder, original_root)
        self.assertEqual(upload.call_args.args[1], f"{original_root}/Images")


if __name__ == "__main__":
    unittest.main()
