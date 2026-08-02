"""Kimi/NVIDIA NIM request and structured-response tests."""

import json
import unittest
from unittest.mock import Mock, patch

from app.services.kimi_service import KimiError, generate_book_game_bundle


class KimiServiceTests(unittest.TestCase):
    def setUp(self):
        self.book = {
            "title": "A Small Garden",
            "age_group": "7-9",
            "reading_level": "beginner",
            "text_content": "Mina planted a seed. The seed grew into a sunflower.",
        }
        self.config = {
            "KIMI_API_KEY": "test-only-key",
            "KIMI_MODEL": "moonshotai/kimi-k2.6",
            "KIMI_API_URL": "https://integrate.api.nvidia.com/v1/chat/completions",
            "KIMI_REQUEST_TIMEOUT": 30,
        }

    @patch("app.services.kimi_service.requests.post")
    def test_calls_nvidia_chat_completions_and_parses_json(self, post):
        expected = {
            "questions": [],
            "word_puzzle_words": [],
            "spelling_words": [],
        }
        response = Mock(ok=True)
        response.json.return_value = {
            "choices": [{"message": {"content": f"```json\n{json.dumps(expected)}\n```"}}]
        }
        post.return_value = response

        generated = generate_book_game_bundle(self.book, self.config, 5, "English")

        self.assertEqual(generated, expected)
        request = post.call_args
        self.assertEqual(request.args[0], self.config["KIMI_API_URL"])
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer test-only-key")
        self.assertEqual(request.kwargs["json"]["model"], "moonshotai/kimi-k2.6")
        self.assertFalse(request.kwargs["json"]["stream"])

    def test_requires_a_server_side_key(self):
        with self.assertRaises(KimiError):
            generate_book_game_bundle(self.book, {}, 5, "English")


if __name__ == "__main__":
    unittest.main()
