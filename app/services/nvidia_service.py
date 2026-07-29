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
