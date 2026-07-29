"""NVIDIA NIM-powered generation for book drafts."""
import json
import re

import requests


class NvidiaError(Exception):
    """An NVIDIA API setup, request, or structured-output error."""


DEFAULT_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"


def _api_key(config):
    key = str(config.get("NVIDIA_API_KEY") or config.get("NVAPI_KEY") or "").strip()
    if not key:
        raise NvidiaError("NVIDIA is not configured. Set NVIDIA_API_KEY on the API server.")
    return key


def _error_message(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or response.reason)
            return str(payload.get("message") or response.reason)
    except (ValueError, AttributeError):
        pass
    return f"NVIDIA returned HTTP {response.status_code}."


def _json_from_content(content):
    """Parse JSON even when a model wraps it in a markdown code fence."""
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def generate_book_draft(age_group, reading_level, idea, config):
    """Generate a reviewed-ready children's story through NVIDIA NIM."""
    idea = str(idea or "").strip()
    if not idea:
        raise NvidiaError("A story idea is required.")

    prompt = f"""
Write an original children's story for the TeachAlike reading app.

Age group: {str(age_group or '').strip()}
Reading level: {str(reading_level or '').strip()}
Story idea: {idea[:500]}

Return a short, engaging story with a clear beginning, middle, and ending.
Use warm, age-appropriate language, vivid but safe imagery, and short paragraphs.
Do not include a preface, lesson-plan notes, markdown headings, or questions.
Return ONLY valid JSON with exactly these string fields:
{{"title": "Story title", "text_content": "The complete story text"}}
Do not wrap the JSON in markdown fences.
"""

    url = str(config.get("NVIDIA_API_URL", DEFAULT_API_URL)).strip() or DEFAULT_API_URL
    model = str(config.get("NVIDIA_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    try:
        response = requests.post(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {_api_key(config)}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You create safe, original children's books and follow JSON output instructions exactly.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.8,
                "max_tokens": 2200,
                "stream": False,
            },
            timeout=(10, max(1, int(config.get("NVIDIA_REQUEST_TIMEOUT", 60)))),
        )
    except requests.RequestException as exc:
        raise NvidiaError("NVIDIA could not be reached while creating this book draft.") from exc
    if not response.ok:
        raise NvidiaError(f"NVIDIA could not create this book draft: {_error_message(response)}")

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        generated = _json_from_content(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise NvidiaError("NVIDIA returned book content in an unexpected format.") from exc

    title = str(generated.get("title") or "").strip() if isinstance(generated, dict) else ""
    text_content = str(generated.get("text_content") or "").strip() if isinstance(generated, dict) else ""
    if not title or not text_content:
        raise NvidiaError("NVIDIA returned an incomplete book draft.")
    return {"title": title[:200], "text_content": text_content[:30000]}
