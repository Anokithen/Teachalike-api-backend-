"""Seed data stays repeatable and free of external media records."""

import os
import tempfile
import unittest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.asset_model import Asset
from app.models.book_model import Book
from app.models.book_narration_model import BookNarration
from app.models.child_model import Child
from app.models.feedback_model import Feedback
from app.models.parent_model import Parent
from app.models.reading_session_model import ReadingSession
from app.models.voice_profile_model import VoiceProfile
from app.models.teacher_profile_model import APPROVAL_APPROVED
from seed import seed_database


class SeedDataTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        os.unlink(self.database_path)

    def test_seed_is_idempotent_and_contains_no_voices_or_media(self):
        first_counts, _credentials = seed_database()
        second_counts, _credentials = seed_database()

        self.assertEqual(first_counts["accounts"], 4)
        self.assertEqual(first_counts["children"], 4)
        self.assertEqual(first_counts["books"], 6)
        self.assertEqual(first_counts["reading_sessions"], 8)
        self.assertEqual(second_counts["accounts"], 0)
        self.assertEqual(second_counts["children"], 0)
        self.assertEqual(second_counts["books"], 0)
        self.assertEqual(second_counts["reading_sessions"], 0)

        self.assertEqual(Parent.query.count(), 4)
        self.assertEqual(Child.query.count(), 4)
        self.assertEqual(Book.query.count(), 6)
        self.assertEqual(ReadingSession.query.count(), 8)
        self.assertEqual(VoiceProfile.query.count(), 0)
        self.assertEqual(BookNarration.query.count(), 0)
        self.assertEqual(Asset.query.count(), 0)
        self.assertEqual(Parent.query.filter_by(role="teacher").one().teacher_profile.approval_status, APPROVAL_APPROVED)

        for account in Parent.query.all():
            self.assertIsNone(account.profile_image_url)
            self.assertIsNone(account.profile_image_public_id)
        for child in Child.query.all():
            self.assertIsNone(child.profile_image_url)
            self.assertIsNone(child.profile_image_public_id)
        for book in Book.query.all():
            self.assertIsNone(book.cover_image_url)
            self.assertIsNone(book.video_url)
            self.assertIsNone(book.content_url)
            self.assertFalse(book.image_urls)
        for session in ReadingSession.query.all():
            self.assertIsNone(session.voice_profile_id)
        for feedback in Feedback.query.all():
            self.assertIsNone(feedback.audio_url)


if __name__ == "__main__":
    unittest.main()
