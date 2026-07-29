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


def _error_message(response):
    try:
        payload = response.json()
        return str(payload.get("error", {}).get("message") or payload.get("message") or response.reason)
    except (ValueError, AttributeError):
        return f"Gemini returned HTTP {response.status_code}."


def _normalise_question(raw, book_text):
    if not isinstance(raw, dict):
        return None
    word = str(raw.get("word") or "").strip()
    question = str(raw.get("question") or "").strip()
    answer = str(raw.get("answer") or "").strip()
    options = raw.get("options")
    if not word or not question or not answer or not isinstance(options, list):
        return None

    options = [str(option).strip() for option in options if str(option).strip()]
    unique_options = []
    seen_options = set()
    for option in options:
        if option.casefold() not in seen_options:
            unique_options.append(option)
            seen_options.add(option.casefold())
    options = unique_options
    matching_answer = next((option for option in options if option.casefold() == answer.casefold()), None)
    if len(options) != 4 or matching_answer is None:
        return None
    # Keep generated questions grounded in the actual book. Case-insensitive
    # matching lets Gemini return normal title casing for a story word.
    word_pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
    if not re.search(word_pattern, book_text, flags=re.IGNORECASE):
        return None
    return {
        "word": word,
        "question": question[:240],
        "options": [option[:80] for option in options],
        "answer": matching_answer[:80],
        "hint": str(raw.get("hint") or "Look back at how this word was used in the story.").strip()[:180],
        "explanation": str(raw.get("explanation") or "This answer matches the way the word was used in the story.").strip()[:240],
    }
