"""Legacy Gemini provider for optional book-draft generation."""
import json

import requests


class GeminiError(Exception):
    """A Gemini setup, API, or structured-output error."""


API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


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


def generate_book_draft(age_group, reading_level, idea, config):
    """Generate a reviewed-ready story draft without exposing Gemini to the browser."""
    idea = str(idea or "").strip()
    if not idea:
        raise GeminiError("A story idea is required.")
    prompt = f"""
Write an original children's story for the TeachAlike reading app.

Age group: {str(age_group or '').strip()}
Reading level: {str(reading_level or '').strip()}
Story idea: {idea[:500]}

Return a short, engaging story with a clear beginning, middle, and ending.
Use warm, age-appropriate language, vivid but safe imagery, and short paragraphs.
Do not include a preface, lesson-plan notes, markdown headings, or questions.
Return only the story title and story text in the requested JSON format.
"""
    model = str(config.get("GEMINI_MODEL", "gemini-2.5-flash")).strip()
    try:
        response = requests.post(
            f"{API_BASE_URL}/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": _api_key(config)},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": BOOK_DRAFT_SCHEMA,
                    "temperature": 0.8,
                    "maxOutputTokens": 1800,
                },
            },
            timeout=(10, max(1, int(config.get("GEMINI_REQUEST_TIMEOUT", 45)))),
        )
    except requests.RequestException as exc:
        raise GeminiError("Gemini could not be reached while creating this book draft.") from exc
    if not response.ok:
        raise GeminiError(f"Gemini could not create this book draft: {_error_message(response)}")

    try:
        payload = response.json()
        parts = payload["candidates"][0]["content"]["parts"]
        generated_text = "".join(str(part.get("text") or "") for part in parts)
        generated = json.loads(generated_text)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiError("Gemini returned book content in an unexpected format.") from exc

    title = str(generated.get("title") or "").strip() if isinstance(generated, dict) else ""
    text_content = str(generated.get("text_content") or "").strip() if isinstance(generated, dict) else ""
    if not title or not text_content:
        raise GeminiError("Gemini returned an incomplete book draft.")
    return {"title": title[:200], "text_content": text_content[:30000]}
