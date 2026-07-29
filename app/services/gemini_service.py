"""Gemini-powered generation for book-linked learning activities."""
import json
import re

import requests


class GeminiError(Exception):
    """A Gemini setup, API, or structured-output error."""


API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


QUESTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "word": {"type": "STRING"},
                    "question": {"type": "STRING"},
                    "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "answer": {"type": "STRING"},
                    "hint": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                },
                "required": ["word", "question", "options", "answer", "hint", "explanation"],
            },
        }
    },
    "required": ["questions"],
}


BOOK_DRAFT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "text_content": {"type": "STRING"},
    },
    "required": ["title", "text_content"],
}


def _api_key(config):
    key = str(config.get("GEMINI_API_KEY") or config.get("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise GeminiError("Gemini is not configured. Set GEMINI_API_KEY on the API server.")
    return key
