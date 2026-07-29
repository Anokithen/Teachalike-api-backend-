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
