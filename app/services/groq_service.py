"""Groq model discovery and OpenAI-compatible chat completions."""

import json
import re

import requests


DEFAULT_API_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqError(Exception):
    """A safe, user-facing Groq configuration, request, or response error."""


def _api_key(config):
    key = str(config.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise GroqError("Groq is not configured. Set GROQ_API_KEY on the API server.")
    return key


def _base_url(config):
    return str(config.get("GROQ_API_URL") or DEFAULT_API_BASE_URL).strip().rstrip("/")


def _headers(config):
    return {
        "Authorization": f"Bearer {_api_key(config)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


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
    return f"Groq returned HTTP {response.status_code}."


def _json_from_content(content):
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


def _models_response(config):
    try:
        response = requests.get(
            f"{_base_url(config)}/models",
            headers=_headers(config),
            timeout=(10, max(5, int(config.get("GROQ_REQUEST_TIMEOUT", 20)))),
        )
    except requests.RequestException as err:
        raise GroqError("Groq could not be reached while loading available models.") from err

    if not response.ok:
        raise GroqError(f"Groq could not load available models: {_error_message(response)}")
    return response


def _parse_models(response):
    records = response.json().get("data", [])
    if not isinstance(records, list):
        raise TypeError
    models = []
    for record in records:
        model_id = str(record.get("id") or "").strip()
        lowered_id = model_id.lower()
        if not model_id or record.get("active", True) is False:
            continue
        # These models are for audio or moderation, not chat-completion
        # prompts used by book creation and pronunciation scoring.
        if any(marker in lowered_id for marker in ("whisper", "guard", "tts")):
            continue
        models.append(
            {
                "id": model_id,
                "owned_by": str(record.get("owned_by") or "Groq"),
                "context_window": record.get("context_window"),
            }
        )
    return sorted(models, key=lambda model: model["id"].lower())


def list_models(config):
    """Return the currently active Groq chat-capable model metadata."""
    response = _models_response(config)

    try:
        return _parse_models(response)
    except (AttributeError, TypeError, ValueError) as err:
        raise GroqError("Groq returned an invalid model list.") from err
