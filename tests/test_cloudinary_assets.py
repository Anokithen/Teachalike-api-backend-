"""Cloudinary asset tests use mocks only; no live network calls."""

import io
import os
import tempfile
import unittest
from itertools import count
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import create_access_token
from werkzeug.datastructures import FileStorage

from app import create_app
from app.config import Config
from app.controllers import asset_controller
from app.extensions import db
from app.models.asset_model import (
    Asset,
    BOOK_VIDEO,
    CHILD_PROFILE_IMAGE,
    GENERATED_BOOK_AUDIO,
    USER_PROFILE_IMAGE,
    VOICE_PROFILE,
)
from app.models.book_model import Book
from app.models.book_narration_model import BookNarration
from app.models.child_model import Child
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT
from app.models.voice_profile_model import STATUS_READY, VoiceProfile
from app.services.cloudinary_path_service import (
    get_book_video_folder,
    get_child_profile_folder,
    get_generated_book_audio_folder,
    get_user_profile_folder,
    get_voice_profile_folder,
    sanitize_folder_segment,
)
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    get_asset_metadata,
    replace_asset,
    signed_voice_delivery_url,
    upload_asset,
    upload_book_narration,
    upload_profile_image,
    upload_voice_sample,
    validate_uploaded_file,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
MP4 = b"\0\0\0\x18ftypisom" + b"\0" * 64
WAV = b"RIFF" + b"\0\0\0\0" + b"WAVEfmt " + b"\0" * 64
MP3 = b"ID3" + b"\0" * 64


class ImportContractTests(unittest.TestCase):
    def test_app_and_asset_controller_import(self):
        self.assertTrue(callable(create_app))
        self.assertTrue(callable(asset_controller.upload_user_profile_image))

    def test_required_cloudinary_service_symbols_exist(self):
        for symbol in (
            upload_asset,
            delete_asset,
            replace_asset,
            get_asset_metadata,
        ):
            self.assertTrue(callable(symbol))
        self.assertTrue(issubclass(CloudinaryServiceError, Exception))


class PathServiceTests(unittest.TestCase):
    def test_folder_mappings_include_ids(self):
        self.assertEqual(get_user_profile_folder(7), "teachalike/7/Image/Profile")
        self.assertEqual(
            get_child_profile_folder(7, 4, "Sam Lee"),
            "teachalike/7/Image/Children_profile/4_sam_lee",
        )
        self.assertEqual(
            get_voice_profile_folder(7), "teachalike/7/Audio/Voice_profiles"
        )
        self.assertEqual(
            get_generated_book_audio_folder(7, 9, "A Book"),
            "teachalike/7/Audio/Generated_Books_Audio/9_a_book",
        )
        self.assertEqual(
            get_book_video_folder(7, 2, 9, "A Book"),
            "teachalike/7/Video/2/9_a_book",
        )

    def test_sanitization_blocks_traversal_and_caps_length(self):
        result = sanitize_folder_segment("../../A\\B / C")
        self.assertNotIn("..", result)
        self.assertNotIn("/", result)
        self.assertNotIn("\\", result)
        self.assertEqual(result, "a_b_c")
        self.assertLessEqual(len(sanitize_folder_segment("x" * 500)), 80)
        self.assertEqual(sanitize_folder_segment("///"), "unnamed")

    def test_duplicate_names_still_have_distinct_paths(self):
        self.assertNotEqual(
            get_child_profile_folder(1, 10, "Alex"),
            get_child_profile_folder(1, 11, "Alex"),
        )
        self.assertNotEqual(
            get_generated_book_audio_folder(1, 10, "Same"),
            get_generated_book_audio_folder(1, 11, "Same"),
        )

    @patch("app.services.cloudinary_service._cloudinary_modules")
    def test_signed_audio_delivery_keeps_uploaded_format(self, modules):
        cloudinary_url = MagicMock(return_value=("https://signed.test/audio.wav", {}))
        modules.return_value = SimpleNamespace(
            config=MagicMock(),
            utils=SimpleNamespace(cloudinary_url=cloudinary_url),
        )

        result = signed_voice_delivery_url(
            "teachalike/users_voiceprofiles/owner/sample",
            "https://res.cloudinary.test/video/authenticated/sample.wav?token=old",
            {
                "CLOUDINARY_CLOUD_NAME": "test",
                "CLOUDINARY_API_KEY": "test",
                "CLOUDINARY_API_SECRET": "test",
            },
        )

        self.assertEqual(result, "https://signed.test/audio.wav")
        self.assertEqual(cloudinary_url.call_args.kwargs["format"], "wav")

    @patch("app.services.cloudinary_service._cloudinary_modules")
    def test_signed_narration_delivery_keeps_mp3_format(self, modules):
        cloudinary_url = MagicMock(return_value=("https://signed.test/audio.mp3", {}))
        modules.return_value = SimpleNamespace(
            config=MagicMock(),
            utils=SimpleNamespace(cloudinary_url=cloudinary_url),
        )

        signed_voice_delivery_url(
            "teachalike/generated_booksaudio/owner/book/voice_profile_1",
            "https://res.cloudinary.test/video/authenticated/narration.mp3",
            {
                "CLOUDINARY_CLOUD_NAME": "test",
                "CLOUDINARY_API_KEY": "test",
                "CLOUDINARY_API_SECRET": "test",
            },
        )

        self.assertEqual(cloudinary_url.call_args.kwargs["format"], "mp3")


class CloudinaryServiceTests(unittest.TestCase):
    def test_mp3_validation_accepts_browser_mime_variants(self):
        for mime_type in (
            "audio/mpeg",
            "audio/x-mpeg",
            "audio/x-mp3",
            "application/octet-stream",
        ):
            with self.subTest(mime_type=mime_type):
                upload = FileStorage(
                    stream=io.BytesIO(MP3),
                    filename="voice.mp3",
                    content_type=mime_type,
                )
                self.assertEqual(validate_uploaded_file(upload, "audio"), "mp3")

    def test_generic_audio_mime_still_requires_valid_magic_bytes(self):
        upload = FileStorage(
            stream=io.BytesIO(b"not an mp3"),
            filename="voice.mp3",
            content_type="application/octet-stream",
        )
        with self.assertRaisesRegex(ValueError, "contents do not match"):
            validate_uploaded_file(upload, "audio")

    def test_missing_configuration_is_sanitized(self):
        with patch("app.services.cloudinary_service._cloudinary_modules") as modules:
            modules.return_value = SimpleNamespace(config=MagicMock())
            with self.assertRaisesRegex(
                CloudinaryServiceError,
                "Cloudinary is not configured",
            ):
                upload_asset(
                    io.BytesIO(PNG),
                    "teachalike/1/Image/Profile",
                    resource_type="image",
                    config={},
                )

    @patch("app.services.cloudinary_service._cloudinary_modules")
    def test_replace_invalidates_and_delete_uses_exact_identity(self, modules):
        uploader = MagicMock()
        uploader.upload.return_value = {
            "asset_id": "asset",
            "public_id": "teachalike/1/Image/Profile/profile",
            "secure_url": "https://res.cloudinary.test/profile.png",
            "resource_type": "image",
            "type": "upload",
            "asset_folder": "teachalike/1/Image/Profile",
        }
        uploader.destroy.return_value = {"result": "ok"}
        modules.return_value = SimpleNamespace(
            config=MagicMock(),
            uploader=uploader,
        )
        config = {
            "CLOUDINARY_CLOUD_NAME": "test",
            "CLOUDINARY_API_KEY": "test",
            "CLOUDINARY_API_SECRET": "test",
        }

        replace_asset(
            io.BytesIO(PNG),
            "teachalike/1/Image/Profile",
            "image",
            "teachalike/1/Image/Profile/profile",
            config=config,
        )
        self.assertTrue(uploader.upload.call_args.kwargs["overwrite"])
        self.assertTrue(uploader.upload.call_args.kwargs["invalidate"])

        result = delete_asset(
            "teachalike/1/Image/Profile/profile",
            "image",
            "upload",
            config=config,
        )
        self.assertEqual(result["result"], "ok")
        uploader.destroy.assert_called_once_with(
            "teachalike/1/Image/Profile/profile",
            resource_type="image",
            type="upload",
            invalidate=True,
        )

    @patch("app.services.cloudinary_service._cloudinary_modules")
    def test_get_asset_metadata_is_normalized(self, modules):
        api = MagicMock()
        api.resource.return_value = {
            "asset_id": "asset",
            "public_id": "public",
            "secure_url": "https://res.cloudinary.test/file.mp4",
            "resource_type": "video",
            "type": "upload",
            "bytes": 100,
        }
        modules.return_value = SimpleNamespace(config=MagicMock(), api=api)
        metadata = get_asset_metadata(
            "public",
            "video",
            config={
                "CLOUDINARY_CLOUD_NAME": "test",
                "CLOUDINARY_API_KEY": "test",
                "CLOUDINARY_API_SECRET": "test",
            },
        )
        self.assertEqual(metadata["asset_id"], "asset")
        self.assertEqual(metadata["secure_url"], "https://res.cloudinary.test/file.mp4")
        self.assertNotIn("url", metadata)

    def test_legacy_helpers_delegate_to_central_upload(self):
        voice_file = FileStorage(
            stream=io.BytesIO(WAV),
            filename="voice.wav",
            content_type="audio/wav",
        )
        metadata = {
            "asset_id": "asset",
            "public_id": "canonical",
            "secure_url": "https://res.cloudinary.test/canonical.wav",
            "resource_type": "video",
            "delivery_type": "authenticated",
            "format": "wav",
            "bytes": len(WAV),
            "width": None,
            "height": None,
            "duration": 1.0,
            "asset_folder": "teachalike/7/Audio/Voice_profiles",
            "original_filename": "voice.wav",
        }
        with patch(
            "app.services.cloudinary_service.upload_asset",
            return_value=metadata,
        ) as upload:
            upload_voice_sample(
                voice_file,
                7,
                {},
                voice_profile_id=12,
            )
        self.assertEqual(
            upload.call_args.args[1],
            "teachalike/7/Audio/Voice_profiles",
        )
        self.assertEqual(
            upload.call_args.kwargs["public_id"],
            "teachalike/7/Audio/Voice_profiles/voice_profile_12",
        )

        image_file = FileStorage(
            stream=io.BytesIO(PNG),
            filename="photo.png",
            content_type="image/png",
        )
        image_metadata = {
            **metadata,
            "public_id": "profile",
            "secure_url": "https://res.cloudinary.test/profile.png",
            "resource_type": "image",
            "delivery_type": "upload",
            "format": "png",
            "asset_folder": "teachalike/7/Image/Profile",
        }
        with patch(
            "app.services.cloudinary_service.replace_asset",
            return_value=image_metadata,
        ) as replace:
            upload_profile_image(image_file, "accounts", 7, {})
        self.assertEqual(
            replace.call_args.args[1],
            "teachalike/7/Image/Profile",
        )

    def test_legacy_narration_helper_uses_generation_id(self):
        metadata = {
            "asset_id": "asset",
            "public_id": "canonical",
            "secure_url": "https://res.cloudinary.test/narration.mp3",
            "resource_type": "video",
            "delivery_type": "authenticated",
            "format": "mp3",
            "bytes": len(MP3),
            "width": None,
            "height": None,
            "duration": 1.0,
            "asset_folder": "teachalike/7/Audio/Generated_Books_Audio/3_book",
            "original_filename": "narration.mp3",
        }
        with patch(
            "app.services.cloudinary_service.upload_asset",
            return_value=metadata,
        ) as upload:
            upload_book_narration(
                io.BytesIO(MP3),
                7,
                "Ignored Name",
                3,
                "Book",
                12,
                {},
                generation_id=44,
            )
        self.assertEqual(
            upload.call_args.kwargs["public_id"],
            "teachalike/7/Audio/Generated_Books_Audio/3_book/"
            "voice_12_3_44",
        )


class AssetEndpointTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        Config.CLOUDINARY_CLOUD_NAME = "test"
        Config.CLOUDINARY_API_KEY = "test"
        Config.CLOUDINARY_API_SECRET = "test"
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.owner = Parent(
            name="Owner", email="owner@example.test", password="hash", role=ROLE_PARENT
        )
        self.other = Parent(
            name="Other", email="other@example.test", password="hash", role=ROLE_PARENT
        )
        self.admin = Parent(
            name="Admin", email="admin@example.test", password="hash", role=ROLE_ADMIN
        )
        db.session.add_all([self.owner, self.other, self.admin])
        db.session.flush()
        self.child = Child(
            parent_id=self.owner.id,
            created_by_id=self.owner.id,
            name="Same Name",
            age=8,
        )
        self.book = Book(title="Same Name", age_group="7-9")
        db.session.add_all([self.child, self.book])
        db.session.commit()
        self.client = self.app.test_client()
        self.ids = count(1)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        os.unlink(self.database_path)

    def _headers(self, user):
        return {"Authorization": f"Bearer {create_access_token(identity=user.id)}"}

    def _upload_result(
        self,
        _file,
        asset_folder,
        resource_type="auto",
        public_id=None,
        **kwargs,
    ):
        number = next(self.ids)
        return {
            "asset_id": f"asset-{number}",
            "public_id": public_id or f"unique-{number}",
            "secure_url": (
                f"https://res.cloudinary.test/{number}."
                f"{kwargs.get('format') or ('png' if resource_type == 'image' else 'mp4')}"
            ),
            "resource_type": resource_type,
            "delivery_type": kwargs.get("delivery_type") or "upload",
            "format": kwargs.get("format") or ("png" if resource_type == "image" else "mp4"),
            "bytes": 72,
            "width": 10 if resource_type != "raw" else None,
            "height": 10 if resource_type != "raw" else None,
            "duration": 1.5 if resource_type == "video" else None,
            "asset_folder": asset_folder,
            "original_filename": "upload",
        }

    @patch("app.controllers.asset_controller.upload_asset")
    def test_profile_upload_and_replacement(self, upload):
        upload.side_effect = self._upload_result
        for _ in range(2):
            response = self.client.post(
                "/api/assets/profile-image",
                headers=self._headers(self.owner),
                data={"file": (io.BytesIO(PNG), "photo.png")},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(
            Asset.query.filter_by(owner_user_id=self.owner.id, deleted_at=None).count(),
            1,
        )
        self.assertTrue(upload.call_args.kwargs["public_id"].endswith("/profile"))

    @patch("app.controllers.asset_controller.upload_asset")
    def test_same_filename_different_users_has_distinct_public_ids(self, upload):
        upload.side_effect = self._upload_result
        public_ids = []
        for user in (self.owner, self.other):
            response = self.client.post(
                "/api/assets/profile-image",
                headers=self._headers(user),
                data={"file": (io.BytesIO(PNG), "same.png")},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 201, response.json)
            public_ids.append(upload.call_args.kwargs["public_id"])
        self.assertNotEqual(*public_ids)

    @patch("app.controllers.asset_controller.delete_asset")
    @patch("app.controllers.asset_controller.upload_asset")
    def test_child_rename_replacement_cleans_previous_public_id(
        self, upload, destroy
    ):
        upload.side_effect = self._upload_result
        first = self.client.post(
            f"/api/assets/children/{self.child.id}/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(PNG), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(first.status_code, 201, first.json)
        old_public_id = db.session.get(
            Asset, first.json["data"]["id"]
        ).cloudinary_public_id
        self.child.name = "Renamed Child"
        db.session.commit()
        second = self.client.post(
            f"/api/assets/children/{self.child.id}/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(PNG), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(second.status_code, 201, second.json)
        new_public_id = db.session.get(
            Asset, second.json["data"]["id"]
        ).cloudinary_public_id
        self.assertNotEqual(old_public_id, new_public_id)
        destroy.assert_called_once()

    def test_bad_type_returns_415(self):
        response = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(b"not an image"), "bad.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 415)

    def test_incorrect_mime_type_returns_415(self):
        response = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={
                "file": (
                    io.BytesIO(PNG),
                    "photo.png",
                    "application/octet-stream",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 415)

    def test_incorrect_file_signature_returns_415(self):
        response = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(b"not-png"), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 415)

    def test_oversized_upload_returns_413(self):
        self.app.config["MAX_PROFILE_IMAGE_SIZE_MB"] = 0
        response = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(PNG), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 413)

    def test_missing_cloudinary_configuration_returns_503(self):
        self.app.config.update(
            CLOUDINARY_CLOUD_NAME=None,
            CLOUDINARY_API_KEY=None,
            CLOUDINARY_API_SECRET=None,
        )
        response = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(PNG), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 503, response.json)
        self.assertNotIn("api_secret", response.get_data(as_text=True).lower())

    @patch("app.controllers.asset_controller.upload_asset")
    def test_child_ownership_is_enforced(self, upload):
        upload.side_effect = self._upload_result
        response = self.client.post(
            f"/api/assets/children/{self.child.id}/profile-image",
            headers=self._headers(self.other),
            data={"file": (io.BytesIO(PNG), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 403)
        upload.assert_not_called()

    @patch("app.controllers.asset_controller.upload_asset")
    def test_cloudinary_failure_is_sanitized(self, upload):
        from app.services.cloudinary_service import CloudinaryUploadError

        upload.side_effect = CloudinaryUploadError("sdk secret detail")
        response = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(PNG), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sdk secret detail", response.get_data(as_text=True))

    @patch("app.controllers.asset_controller.delete_asset")
    @patch("app.controllers.asset_controller.upload_asset")
    def test_initial_profile_database_failure_cleans_upload(
        self, upload, destroy
    ):
        from sqlalchemy.exc import SQLAlchemyError

        upload.side_effect = self._upload_result
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("db down")):
            response = self.client.post(
                "/api/assets/profile-image",
                headers=self._headers(self.owner),
                data={"file": (io.BytesIO(PNG), "photo.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 500)
        destroy.assert_called_once()

    @patch("app.controllers.asset_controller.delete_asset")
    @patch("app.controllers.asset_controller.upload_asset")
    def test_replacement_database_failure_preserves_confirmed_replacement(
        self, upload, destroy
    ):
        from sqlalchemy.exc import SQLAlchemyError

        upload.side_effect = self._upload_result
        first = self.client.post(
            "/api/assets/profile-image",
            headers=self._headers(self.owner),
            data={"file": (io.BytesIO(PNG), "first.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(first.status_code, 201, first.json)
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("db down")):
            second = self.client.post(
                "/api/assets/profile-image",
                headers=self._headers(self.owner),
                data={"file": (io.BytesIO(PNG), "second.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(second.status_code, 500)
        destroy.assert_not_called()

    def test_voice_and_narration_upload_endpoints(self):
        with (
            patch(
                "app.controllers.voice_profile_controller.upload_asset",
                side_effect=self._upload_result,
            ) as voice_upload,
            patch(
                "app.controllers.voice_profile_controller.clone_voice",
                return_value="elevenlabs-voice",
            ),
        ):
            voice_response = self.client.post(
                "/api/assets/voice-profiles",
                headers=self._headers(self.owner),
                data={"file": (io.BytesIO(WAV), "voice.wav"), "label": "My voice"},
                content_type="multipart/form-data",
            )
        self.assertEqual(voice_response.status_code, 201, voice_response.json)
        voice_id = voice_response.json["data"]["voice_profile_id"]
        self.assertEqual(
            voice_upload.call_args.args[1],
            f"teachalike/{self.owner.id}/Audio/Voice_profiles",
        )
        self.assertEqual(
            voice_upload.call_args.kwargs["delivery_type"],
            "authenticated",
        )
        with patch(
            "app.controllers.asset_controller.upload_asset",
            side_effect=self._upload_result,
        ) as narration_upload:
            narration_response = self.client.post(
                f"/api/assets/books/{self.book.id}/narrations",
                headers=self._headers(self.owner),
                data={
                    "file": (io.BytesIO(WAV), "narration.wav"),
                    "voice_profile_id": str(voice_id),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(
            narration_response.status_code, 201, narration_response.json
        )
        self.assertIsNotNone(narration_response.json["data"]["generation_id"])
        self.assertIn(
            f"/voice_{voice_id}_{self.book.id}_",
            narration_upload.call_args.kwargs["public_id"],
        )
        self.assertEqual(
            Asset.query.filter_by(asset_category=VOICE_PROFILE).count(),
            1,
        )
        self.assertEqual(
            Asset.query.filter_by(asset_category=GENERATED_BOOK_AUDIO).count(),
            1,
        )

    def test_voice_profile_accepts_mp3_larger_than_five_mb(self):
        mp3_sample = MP3 + (b"\0" * (6 * 1024 * 1024))
        with (
            patch(
                "app.controllers.voice_profile_controller.upload_asset",
                side_effect=self._upload_result,
            ) as upload,
            patch(
                "app.controllers.voice_profile_controller.clone_voice",
                return_value="elevenlabs-large-mp3",
            ),
        ):
            response = self.client.post(
                "/api/voice-profiles",
                headers=self._headers(self.owner),
                data={
                    "audio": (
                        io.BytesIO(mp3_sample),
                        "voice.mp3",
                        "audio/x-mpeg",
                    )
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(upload.call_args.kwargs["format"], "mp3")
        response.request.environ["wsgi.input"].close()
        response.request.close()
        response.close()

    @patch("app.controllers.asset_controller.upload_asset")
    def test_multiple_narrations_and_voice_profiles_are_distinct(self, upload):
        upload.side_effect = self._upload_result
        first_voice = VoiceProfile(
            parent_id=self.owner.id,
            label="One",
            voice_sample_url="https://example.test/one.wav",
            cloudinary_public_id="voice-one",
            status=STATUS_READY,
        )
        second_voice = VoiceProfile(
            parent_id=self.owner.id,
            label="Two",
            voice_sample_url="https://example.test/two.wav",
            cloudinary_public_id="voice-two",
            status=STATUS_READY,
        )
        db.session.add_all([first_voice, second_voice])
        db.session.commit()

        generation_ids = []
        for profile in (first_voice, first_voice, second_voice):
            response = self.client.post(
                f"/api/assets/books/{self.book.id}/narrations",
                headers=self._headers(self.owner),
                data={
                    "file": (io.BytesIO(MP3), "narration.mp3"),
                    "voice_profile_id": str(profile.id),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 201, response.json)
            generation_ids.append(response.json["data"]["generation_id"])

        self.assertEqual(len(set(generation_ids)), 3)
        public_ids = [
            asset.cloudinary_public_id
            for asset in Asset.query.filter_by(
                asset_category=GENERATED_BOOK_AUDIO
            ).all()
        ]
        self.assertEqual(len(set(public_ids)), 3)
        self.assertTrue(
            any(f"voice_{second_voice.id}_{self.book.id}_" in item for item in public_ids)
        )

    def test_legacy_routes_delegate_without_old_folders(self):
        with patch(
            "app.controllers.asset_controller.upload_asset",
            side_effect=self._upload_result,
        ) as upload:
            parent_response = self.client.post(
                "/api/parents/me/profile-image",
                headers=self._headers(self.owner),
                data={"profile_image": (io.BytesIO(PNG), "profile.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(parent_response.status_code, 200, parent_response.json)
        self.assertEqual(
            upload.call_args.args[1],
            f"teachalike/{self.owner.id}/Image/Profile",
        )

        with patch(
            "app.controllers.asset_controller.upload_asset",
            side_effect=self._upload_result,
        ) as upload:
            child_response = self.client.post(
                f"/api/children/{self.child.id}/profile-image",
                headers=self._headers(self.owner),
                data={"profile_image": (io.BytesIO(PNG), "profile.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(child_response.status_code, 200, child_response.json)
        self.assertIn(
            f"/{self.child.id}_same_name",
            upload.call_args.args[1],
        )

        with (
            patch(
                "app.controllers.voice_profile_controller.upload_asset",
                side_effect=self._upload_result,
            ) as upload,
            patch(
                "app.controllers.voice_profile_controller.clone_voice",
                return_value="elevenlabs-legacy",
            ),
        ):
            voice_response = self.client.post(
                "/api/voice-profiles",
                headers=self._headers(self.owner),
                data={"audio": (io.BytesIO(WAV), "voice.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(voice_response.status_code, 201, voice_response.json)
        self.assertEqual(
            upload.call_args.args[1],
            f"teachalike/{self.owner.id}/Audio/Voice_profiles",
        )
        self.assertNotIn(
            "users_voiceprofiles",
            upload.call_args.kwargs["public_id"],
        )

    @patch(
        "app.controllers.book_narration_controller._enqueue_narration",
        return_value=True,
    )
    def test_background_narration_starts_with_canonical_public_id(self, _enqueue):
        profile = VoiceProfile(
            parent_id=self.owner.id,
            label="Narrator",
            voice_sample_url="https://example.test/voice.wav",
            cloudinary_public_id="voice",
            elevenlabs_voice_id="elevenlabs",
            status=STATUS_READY,
        )
        self.book.text_content = "A short story."
        db.session.add(profile)
        db.session.commit()
        response = self.client.post(
            f"/api/books/{self.book.id}/narrations",
            headers=self._headers(self.owner),
            json={"voice_profile_id": profile.id},
        )
        self.assertEqual(response.status_code, 201, response.json)
        generation = db.session.get(
            BookNarration,
            response.json["book_narration"]["id"],
        )
        self.assertEqual(
            generation.cloudinary_public_id,
            (
                f"teachalike/{self.owner.id}/Audio/Generated_Books_Audio/"
                f"{self.book.id}_same_name/voice_{profile.id}_{self.book.id}_{generation.id}"
            ),
        )

    def test_background_worker_persists_asset_metadata(self):
        from app.controllers.book_narration_controller import _generate_narration

        profile = VoiceProfile(
            parent_id=self.owner.id,
            label="Worker voice",
            voice_sample_url="https://example.test/voice.wav",
            cloudinary_public_id="worker-voice",
            elevenlabs_voice_id="elevenlabs-worker",
            status=STATUS_READY,
        )
        self.book.text_content = "Worker story."
        db.session.add(profile)
        db.session.flush()
        generation = BookNarration(
            book_id=self.book.id,
            voice_profile_id=profile.id,
            status="processing",
            cloudinary_public_id="pending-public-id",
        )
        db.session.add(generation)
        db.session.commit()
        metadata = self._upload_result(
            io.BytesIO(MP3),
            get_generated_book_audio_folder(
                self.owner.id,
                self.book.id,
                self.book.title,
            ),
            resource_type="video",
            public_id=(
                f"teachalike/{self.owner.id}/Audio/Generated_Books_Audio/"
                f"{self.book.id}_same_name/"
                f"voice_{profile.id}_{self.book.id}_{generation.id}"
            ),
            delivery_type="authenticated",
            format="mp3",
        )
        with (
            patch(
                "app.controllers.book_narration_controller.synthesize_narration"
            ),
            patch(
                "app.controllers.book_narration_controller.upload_book_narration",
                return_value=metadata,
            ),
        ):
            _generate_narration(self.app, generation.id)

        db.session.expire_all()
        persisted = db.session.get(BookNarration, generation.id)
        self.assertEqual(persisted.status, STATUS_READY)
        asset = Asset.query.filter_by(
            generation_id=generation.id,
            asset_category=GENERATED_BOOK_AUDIO,
        ).one()
        self.assertEqual(asset.cloudinary_delivery_type, "authenticated")
        self.assertEqual(asset.duration_seconds, 1.5)

    @patch("app.controllers.asset_controller.delete_asset")
    def test_referenced_voice_profile_cannot_be_deleted(self, destroy):
        profile = VoiceProfile(
            parent_id=self.owner.id,
            label="In use",
            voice_sample_url="https://example.test/voice.wav",
            cloudinary_public_id="voice-in-use",
            status=STATUS_READY,
        )
        db.session.add(profile)
        db.session.flush()
        narration = BookNarration(
            book_id=self.book.id,
            voice_profile_id=profile.id,
            status=STATUS_READY,
            narration_audio_url="https://example.test/narration.mp3",
            cloudinary_public_id="narration-in-use",
        )
        db.session.add(narration)
        db.session.commit()
        response = self.client.delete(
            f"/api/voice-profiles/{profile.id}",
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 422, response.json)
        destroy.assert_not_called()

    def test_voice_audio_is_streamed_through_api_with_range_support(self):
        profile = VoiceProfile(
            parent_id=self.owner.id,
            label="Playback",
            voice_sample_url="https://res.cloudinary.test/private/voice.wav",
            cloudinary_public_id="private-voice",
            status=STATUS_READY,
        )
        db.session.add(profile)
        db.session.commit()
        upstream = MagicMock()
        upstream.status_code = 206
        upstream.headers = {
            "Content-Type": "audio/wav",
            "Content-Length": "4",
            "Content-Range": "bytes 0-3/8",
            "Accept-Ranges": "bytes",
        }
        upstream.iter_content.return_value = iter([b"RIFF"])
        headers = {
            **self._headers(self.owner),
            "Range": "bytes=0-3",
        }
        with (
            patch(
                "app.services.cloudinary_service.signed_voice_delivery_url",
                return_value="https://res.cloudinary.test/signed.wav",
            ),
            patch(
                "app.services.cloudinary_service.requests.get",
                return_value=upstream,
            ) as get,
        ):
            response = self.client.get(
                f"/api/voice-profiles/{profile.id}/audio",
                headers=headers,
            )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"RIFF")
        self.assertEqual(response.content_type, "audio/wav")
        self.assertEqual(response.headers["Content-Range"], "bytes 0-3/8")
        self.assertNotIn("Location", response.headers)
        self.assertEqual(
            get.call_args.kwargs["headers"],
            {"Range": "bytes=0-3"},
        )
        response.close()

    def test_narration_audio_is_streamed_through_api(self):
        profile = VoiceProfile(
            parent_id=self.owner.id,
            label="Narration playback",
            voice_sample_url="https://res.cloudinary.test/private/voice.wav",
            cloudinary_public_id="private-voice-narration",
            status=STATUS_READY,
        )
        db.session.add(profile)
        db.session.flush()
        narration = BookNarration(
            book_id=self.book.id,
            voice_profile_id=profile.id,
            status=STATUS_READY,
            narration_audio_url="https://res.cloudinary.test/private/story.mp3",
            cloudinary_public_id="private-story",
        )
        db.session.add(narration)
        db.session.commit()
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {
            "Content-Type": "audio/mpeg",
            "Content-Length": str(len(MP3)),
            "Accept-Ranges": "bytes",
        }
        upstream.iter_content.return_value = iter([MP3])
        with (
            patch(
                "app.services.cloudinary_service.signed_voice_delivery_url",
                return_value="https://res.cloudinary.test/signed.mp3",
            ),
            patch(
                "app.services.cloudinary_service.requests.get",
                return_value=upstream,
            ),
        ):
            response = self.client.get(
                f"/api/book-narrations/{narration.id}/audio",
                headers=self._headers(self.owner),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, MP3)
        self.assertEqual(response.content_type, "audio/mpeg")
        self.assertNotIn("Location", response.headers)
        response.close()

    def test_private_audio_upstream_error_is_sanitized(self):
        profile = VoiceProfile(
            parent_id=self.owner.id,
            label="Unavailable playback",
            voice_sample_url="https://res.cloudinary.test/private/voice.wav",
            cloudinary_public_id="unavailable-private-voice",
            status=STATUS_READY,
        )
        db.session.add(profile)
        db.session.commit()
        upstream = MagicMock()
        upstream.status_code = 403
        upstream.headers = {}
        with (
            patch(
                "app.services.cloudinary_service.signed_voice_delivery_url",
                return_value="https://res.cloudinary.test/signed.wav",
            ),
            patch(
                "app.services.cloudinary_service.requests.get",
                return_value=upstream,
            ),
        ):
            response = self.client.get(
                f"/api/voice-profiles/{profile.id}/audio",
                headers=self._headers(self.owner),
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("403", response.get_data(as_text=True))
        upstream.close.assert_called_once()

    @patch("app.controllers.asset_controller.upload_asset")
    def test_admin_video_upload_validates_book(self, upload):
        upload.side_effect = self._upload_result
        missing = self.client.post(
            "/api/admin/books/999/videos",
            headers=self._headers(self.admin),
            data={"file": (io.BytesIO(MP4), "video.mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(missing.status_code, 404)
        response = self.client.post(
            f"/api/admin/books/{self.book.id}/videos",
            headers=self._headers(self.admin),
            data={"file": (io.BytesIO(MP4), "video.mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(
            upload.call_args.args[1],
            (
                f"teachalike/{self.admin.id}/Video/{self.admin.id}/"
                f"{self.book.id}_same_name"
            ),
        )
        self.assertEqual(
            response.json["data"]["duration_seconds"],
            1.5,
        )
        self.assertEqual(
            db.session.get(Book, self.book.id).video_url,
            response.json["data"]["url"],
        )

    def test_legacy_book_media_endpoint_rejects_unscoped_video(self):
        response = self.client.post(
            "/api/admin/book-media",
            headers=self._headers(self.admin),
            data={
                "file": (io.BytesIO(MP4), "video.mp4"),
                "media_type": "video",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422, response.json)

    @patch("app.services.cloudinary_service.upload_asset")
    def test_legacy_book_image_upload_uses_user_root(self, upload):
        upload.side_effect = self._upload_result
        response = self.client.post(
            "/api/admin/book-media",
            headers=self._headers(self.admin),
            data={
                "file": (io.BytesIO(PNG), "cover.png"),
                "media_type": "image",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(
            upload.call_args.args[1],
            f"teachalike/{self.admin.id}/Image/Book_media",
        )
        self.assertNotIn("book_media/", upload.call_args.args[1])

    @patch("app.services.cloudinary_service._cloudinary_modules")
    def test_authenticated_delivery_option_reaches_sdk(self, modules):
        uploader = MagicMock()
        uploader.upload.return_value = {
            "asset_id": "sdk-asset",
            "public_id": "voice_profile_1",
            "secure_url": "https://res.cloudinary.test/authenticated/file.wav",
            "resource_type": "video",
            "type": "authenticated",
            "asset_folder": "teachalike/1/Audio/Voice_profiles",
        }
        modules.return_value = SimpleNamespace(
            config=MagicMock(), uploader=uploader
        )
        result = upload_asset(
            io.BytesIO(WAV),
            "teachalike/1/Audio/Voice_profiles",
            resource_type="video",
            public_id="voice_profile_1",
            delivery_type="authenticated",
        )
        self.assertEqual(result["delivery_type"], "authenticated")
        self.assertEqual(
            uploader.upload.call_args.kwargs["type"], "authenticated"
        )
        self.assertEqual(
            uploader.upload.call_args.kwargs["asset_folder"],
            "teachalike/1/Audio/Voice_profiles",
        )
        self.assertEqual(
            uploader.upload.call_args.kwargs["timeout"],
            self.app.config["CLOUDINARY_UPLOAD_TIMEOUT_SECONDS"],
        )
        self.assertNotIn("folder", uploader.upload.call_args.kwargs)

    def test_cross_user_asset_read_is_hidden(self):
        asset = Asset(
            owner_user_id=self.owner.id,
            asset_category=USER_PROFILE_IMAGE,
            cloudinary_asset_id="asset-private",
            cloudinary_public_id="private",
            cloudinary_secure_url="https://example.test/private",
            cloudinary_resource_type="image",
            cloudinary_delivery_type="upload",
            cloudinary_asset_folder="teachalike/1/Image/Profile",
        )
        db.session.add(asset)
        db.session.commit()
        response = self.client.get(
            f"/api/assets/{asset.id}", headers=self._headers(self.other)
        )
        self.assertEqual(response.status_code, 404)
        delete_response = self.client.delete(
            f"/api/assets/{asset.id}",
            headers=self._headers(self.other),
        )
        self.assertEqual(delete_response.status_code, 404)

    @patch("app.controllers.asset_controller.delete_asset")
    def test_delete_is_idempotent_upstream_and_marks_row(self, destroy):
        destroy.return_value = "not found"
        asset = Asset(
            owner_user_id=self.owner.id,
            asset_category=USER_PROFILE_IMAGE,
            cloudinary_asset_id="asset-delete",
            cloudinary_public_id="delete-me",
            cloudinary_secure_url="https://example.test/delete",
            cloudinary_resource_type="image",
            cloudinary_delivery_type="upload",
            cloudinary_asset_folder="teachalike/1/Image/Profile",
        )
        db.session.add(asset)
        db.session.commit()
        response = self.client.delete(
            f"/api/assets/{asset.id}", headers=self._headers(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNotNone(db.session.get(Asset, asset.id).deleted_at)
        second = self.client.delete(
            f"/api/assets/{asset.id}", headers=self._headers(self.owner)
        )
        self.assertEqual(second.status_code, 200, second.json)
        destroy.assert_called_once_with("delete-me", "image", "upload")

    @patch("app.controllers.asset_controller.delete_asset")
    def test_profile_asset_delete_clears_related_account_fields(self, destroy):
        destroy.return_value = "ok"
        self.owner.profile_image_url = "https://example.test/profile"
        self.owner.profile_image_public_id = "profile-public-id"
        asset = Asset(
            owner_user_id=self.owner.id,
            asset_category=USER_PROFILE_IMAGE,
            active_slot=f"user:{self.owner.id}:profile",
            cloudinary_asset_id="asset-profile-delete",
            cloudinary_public_id="profile-public-id",
            cloudinary_secure_url="https://example.test/profile",
            cloudinary_resource_type="image",
            cloudinary_delivery_type="upload",
            cloudinary_asset_folder=f"teachalike/{self.owner.id}/Image/Profile",
        )
        db.session.add(asset)
        db.session.commit()
        response = self.client.delete(
            f"/api/assets/{asset.id}", headers=self._headers(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.json)
        db.session.refresh(self.owner)
        self.assertIsNone(self.owner.profile_image_url)
        self.assertIsNone(self.owner.profile_image_public_id)
        self.assertIsNone(db.session.get(Asset, asset.id).active_slot)


if __name__ == "__main__":
    unittest.main()
