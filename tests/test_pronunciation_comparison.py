"""Deterministic alignment and pronunciation-attempt API regression tests."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from flask_jwt_extended import create_access_token
from sqlalchemy import inspect

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.book_model import Book
from app.models.child_model import Child
from app.models.parent_model import Parent, ROLE_PARENT
from app.models.pronunciation_attempt_model import PronunciationAttempt
from app.models.reading_session_model import ReadingSession
from app.services.groq_service import GroqError
from app.services.pronunciation_comparison_service import compare_pronunciation
from app.security import pronunciation_requests


class PronunciationComparisonServiceTests(unittest.TestCase):
    def compare(self, expected, spoken):
        return compare_pronunciation(expected, spoken, 2)

    def test_exact_match_marks_every_token_correct(self):
        result = self.compare("The rabbit runs.", "The rabbit runs.")
        self.assertEqual([token["status"] for token in result["tokens"]], ["correct"] * 3)
        self.assertEqual(result["text_match_accuracy"], 100)

    def test_case_and_punctuation_do_not_create_false_mistakes(self):
        result = self.compare("Hello, WORLD!", "hello world")
        self.assertEqual(result["summary"]["correct_words"], 2)

    def test_substitution_is_pinpointed(self):
        result = self.compare("The rabbit jumped.", "The rabbit jump.")
        token = result["tokens"][2]
        self.assertEqual((token["status"], token["expected"], token["heard"]), ("substitution", "jumped", "jump"))
        self.assertEqual((token["sentence_index"], token["word_index"]), (0, 2))

    def test_skipped_word_is_a_deletion(self):
        result = self.compare("one two three", "one three")
        self.assertEqual([token["status"] for token in result["tokens"]], ["correct", "deletion", "correct"])

    def test_extra_word_is_an_anchored_insertion(self):
        result = self.compare("one three", "one two three")
        extra = next(token for token in result["tokens"] if token["status"] == "insertion")
        self.assertEqual(extra["heard"], "two")
        self.assertEqual((extra["after_word_index"], extra["before_word_index"]), (0, 1))

    def test_multiple_consecutive_skipped_words_align(self):
        result = self.compare("one two three four", "one four")
        self.assertEqual([token["status"] for token in result["tokens"]], ["correct", "deletion", "deletion", "correct"])

    def test_multiple_consecutive_extra_words_align(self):
        result = self.compare("one four", "one two three four")
        extras = [token for token in result["tokens"] if token["status"] == "insertion"]
        self.assertEqual([token["heard"] for token in extras], ["two", "three"])
        self.assertTrue(all(token["after_word_index"] == 0 and token["before_word_index"] == 1 for token in extras))

    def test_repeated_words_do_not_shift_the_remaining_alignment(self):
        result = self.compare("go go go home", "go go home")
        self.assertEqual(result["summary"]["skipped_words"], 1)
        self.assertEqual(result["tokens"][-1]["expected"], "home")
        self.assertEqual(result["tokens"][-1]["status"], "correct")

    def test_contractions_preserve_apostrophes_and_accept_curly_equivalent(self):
        result = self.compare("I can’t stop.", "i can't stop")
        self.assertEqual(result["summary"]["correct_words"], 3)
        self.assertEqual(result["tokens"][1]["expected"], "can’t")

    def test_multiple_sentences_have_correct_word_positions(self):
        result = self.compare("One two. Three, four!", "One two three five")
        token = result["tokens"][-1]
        self.assertEqual((token["sentence_index"], token["word_index"]), (1, 1))

    def test_character_offsets_slice_the_original_word(self):
        original = "Hello, little rabbit."
        result = self.compare(original, "hello tiny rabbit")
        for token in result["tokens"]:
            if token["expected"] is not None:
                self.assertEqual(original[token["character_start"]:token["character_end"]], token["expected"])

    def test_unicode_text_aligns_without_crashing(self):
        result = self.compare("Cafe\u0301 ශ්‍රී ලංකා 日本語.", "CAFÉ ශ්‍රී ලංකා 日本語")
        self.assertEqual(result["summary"]["words_needing_practice"], 0)

    def test_malicious_text_remains_plain_data(self):
        spoken = '<img src=x onerror="alert(1)"> rabbit'
        result = self.compare("small rabbit", spoken)
        self.assertEqual(result["spoken_text"], spoken)
        self.assertNotIn("html", result)

    def test_long_bounded_input_completes_quickly(self):
        text = " ".join(f"word{index}" for index in range(450))
        started = time.monotonic()
        result = self.compare(text, text)
        self.assertEqual(result["text_match_accuracy"], 100)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_excessive_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "too long"):
            self.compare("word " * 2500, "word")


class PronunciationAttemptApiTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.original_uri = Config.SQLALCHEMY_DATABASE_URI
        self.original_options = Config.SQLALCHEMY_ENGINE_OPTIONS
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.app.config.update(TESTING=True, GROQ_MODEL="test-score-model")
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

        self.parent = self.account("Parent", "pronunciation.parent@example.com")
        self.other = self.account("Other", "pronunciation.other@example.com")
        db.session.flush()
        self.child = Child(parent_id=self.parent.id, name="Reader", age=8)
        self.other_child = Child(parent_id=self.other.id, name="Other reader", age=8)
        self.book = Book(
            title="Rabbit Book",
            age_group="7-9",
            reading_level="beginner",
            text_content="The little rabbit jumped over the log.",
        )
        db.session.add_all([self.child, self.other_child, self.book])
        db.session.flush()
        self.session = ReadingSession(child_id=self.child.id, book_id=self.book.id)
        self.other_session = ReadingSession(child_id=self.other_child.id, book_id=self.book.id)
        db.session.add_all([self.session, self.other_session])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        pronunciation_requests.reset(f"pronunciation:{self.parent.id}:{self.session.id}")
        db.session.remove()
        db.drop_all()
        self.context.pop()
        Config.SQLALCHEMY_DATABASE_URI = self.original_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = self.original_options
        os.unlink(self.database_path)

    @staticmethod
    def account(name, email):
        account = Parent(name=name, email=email, role=ROLE_PARENT)
        account.set_password("SecurePass123!")
        db.session.add(account)
        return account

    def headers(self, account=None):
        account = account or self.parent
        return {"Authorization": f"Bearer {create_access_token(identity=account.id)}"}

    def check(self, transcript="The little rabbit jump over log.", **payload):
        body = {"paragraph_index": 0, "transcript": transcript, **payload}
        with patch(
            "app.controllers.reading_session_controller.score_groq_pronunciation",
            return_value=(91, "Almost there! Try the practice words slowly."),
        ):
            return self.client.post(
                f"/api/reading-sessions/{self.session.id}/pronunciation-check",
                headers=self.headers(),
                json=body,
            )

    def test_response_separates_provider_and_text_match_scores_and_serializes_json(self):
        response = self.check()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["provider_accuracy"], 91)
        self.assertEqual(response.json["accuracy"], 91)
        self.assertEqual(response.json["text_match_accuracy"], 71)
        self.assertEqual(response.json["comparison"]["summary"]["words_needing_practice"], 2)
        attempt = db.session.get(PronunciationAttempt, response.json["attempt_id"])
        self.assertEqual(attempt.comparison_data["tokens"][3]["status"], "substitution")

    def test_empty_and_oversized_transcripts_are_rejected(self):
        self.assertEqual(self.check("  ").status_code, 400)
        self.assertEqual(self.check("x" * 1001).status_code, 400)

    def test_invalid_paragraph_index_is_rejected(self):
        response = self.check(paragraph_index=99)
        self.assertEqual(response.status_code, 400)

    def test_saved_book_paragraph_is_used_not_client_text(self):
        response = self.check(original_text="Client controlled words")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["comparison"]["original_text"], self.book.text_content)

    def test_other_account_cannot_compare_or_read_history(self):
        check = self.client.post(
            f"/api/reading-sessions/{self.session.id}/pronunciation-check",
            headers=self.headers(self.other),
            json={"paragraph_index": 0, "transcript": "words"},
        )
        history = self.client.get(
            f"/api/reading-sessions/{self.session.id}/pronunciation-attempts",
            headers=self.headers(self.other),
        )
        self.assertEqual(check.status_code, 404)
        self.assertEqual(history.status_code, 404)

    def test_completed_session_keeps_existing_rejection_rule(self):
        self.session.completed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        db.session.commit()
        self.assertEqual(self.check().status_code, 400)

    def test_provider_failure_uses_labelled_safe_fallback(self):
        with patch(
            "app.controllers.reading_session_controller.score_groq_pronunciation",
            side_effect=GroqError("unavailable"),
        ):
            response = self.client.post(
                f"/api/reading-sessions/{self.session.id}/pronunciation-check",
                headers=self.headers(),
                json={"paragraph_index": 0, "transcript": self.book.text_content},
            )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["scoring_provider"], "local-fallback")
        self.assertIsNone(response.json["provider_accuracy"])
        self.assertEqual(response.json["accuracy"], response.json["text_match_accuracy"])
        self.assertIn("background noise", response.json["feedback"])

    def test_retry_creates_attempt_without_duplicate_points(self):
        first = self.check()
        second = self.check(self.book.text_content)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertGreater(first.json["points_awarded"], 0)
        self.assertEqual(second.json["points_awarded"], 0)
        self.assertTrue(second.json["already_awarded"])
        self.assertEqual(PronunciationAttempt.query.filter_by(reading_session_id=self.session.id).count(), 2)
        self.assertGreater(second.json["improvement"], 0)

    def test_history_is_newest_first_filterable_and_private(self):
        first = self.check("The little rabbit jump over log.")
        second = self.check(self.book.text_content)
        history = self.client.get(
            f"/api/reading-sessions/{self.session.id}/pronunciation-attempts?paragraph_index=0",
            headers=self.headers(),
        )
        self.assertEqual(history.status_code, 200, history.json)
        ids = [item["id"] for item in history.json["pronunciation_attempts"]]
        self.assertEqual(ids, [second.json["attempt_id"], first.json["attempt_id"]])
        invalid = self.client.get(
            f"/api/reading-sessions/{self.session.id}/pronunciation-attempts?paragraph_index=-1",
            headers=self.headers(),
        )
        self.assertEqual(invalid.status_code, 400)

    def test_attempt_storage_has_no_audio_and_has_required_indexes(self):
        self.check()
        columns = {column["name"] for column in inspect(db.engine).get_columns("pronunciation_attempts")}
        self.assertFalse({"audio", "audio_data", "recording"} & columns)
        indexes = inspect(db.engine).get_indexes("pronunciation_attempts")
        indexed_columns = {column for index in indexes for column in index["column_names"]}
        self.assertTrue({"reading_session_id", "paragraph_index", "created_at"}.issubset(indexed_columns))

    def test_attempts_cascade_when_reading_session_is_deleted(self):
        self.check()
        session_id = self.session.id
        db.session.delete(self.session)
        db.session.commit()
        self.assertEqual(PronunciationAttempt.query.filter_by(reading_session_id=session_id).count(), 0)

    def test_expensive_pronunciation_requests_are_rate_limited(self):
        self.app.config.update(
            PRONUNCIATION_RATE_LIMIT_ATTEMPTS=1,
            PRONUNCIATION_RATE_LIMIT_WINDOW_SECONDS=60,
        )
        first = self.check()
        second = self.check()
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 429, second.json)
        self.assertIn("Retry-After", second.headers)


if __name__ == "__main__":
    unittest.main()
