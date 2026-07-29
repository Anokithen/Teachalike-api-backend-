"""ElevenLabs voice cloning and text-to-speech helpers.

The API key is deliberately read from server configuration and is never sent
to the browser. Audio returned by ElevenLabs is written to a temporary file so
the existing private Cloudinary delivery flow can continue to protect it.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests


class ElevenLabsError(Exception):
    """An ElevenLabs setup, API, or audio-generation error."""


API_BASE_URL = "https://api.elevenlabs.io/v1"


def _api_key(config):
    key = str(config.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise ElevenLabsError(
            "ElevenLabs is not configured. Set ELEVENLABS_API_KEY on the API server."
        )
    return key


def _error_message(response):
    try:
        payload = response.json()
        detail = payload.get("detail", payload.get("message"))
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status")
        if detail:
            return str(detail)
    except (ValueError, requests.RequestException):
        pass
    return f"ElevenLabs returned HTTP {response.status_code}."


def _raise_for_api_error(response, action):
    if response.ok:
        return
    raise ElevenLabsError(f"ElevenLabs could not {action}: {_error_message(response)}")


def _request(method, url, action, **kwargs):
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ElevenLabsError(f"ElevenLabs could not {action}: the service is unreachable.") from exc


def _voice_name(profile_label, owner_name, profile_id=None):
    label = str(profile_label or "").strip()
    owner = str(owner_name or "TeachAlike user").strip()
    suffix = f" #{profile_id}" if profile_id else ""
    # ElevenLabs voice names are user-visible. Keep them short and avoid
    # passing arbitrary whitespace/control characters to the upstream API.
    base_name = label or f"{owner} voice"
    value = re.sub(r"\s+", " ", f"{base_name}{suffix}").strip()
    return value[:80] or "TeachAlike voice"
