"""NVIDIA scoring for a spoken transcript against the target sentence."""

import json
import re

import requests


class NvidiaPronunciationError(Exception):
    """A safe, user-facing NVIDIA pronunciation scoring error."""


def _api_key(config):
    key = str(
        config.get("NVIDIA_PRONUNCIATION_API_KEY")
        or config.get("NVIDIA_ASR_API_KEY")
        or config.get("NVIDIA_API_KEY")
        or config.get("NVAPI_KEY")
        or ""
    ).strip()
    if not key:
        raise NvidiaPronunciationError("NVIDIA pronunciation scoring is not configured.")
    return key


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
