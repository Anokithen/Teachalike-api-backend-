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


def _clone_files(file_obj, filename, mimetype, config, name, description):
    """Create an IVC voice from one uploaded or downloaded audio sample."""
    response = _request(
        "POST",
        f"{API_BASE_URL}/voices/add",
        "clone this voice",
        headers={"xi-api-key": _api_key(config)},
        data={
            "name": name,
            "description": description[:1000],
            "remove_background_noise": "false",
        },
        files={"files": (filename or "voice-sample.wav", file_obj, mimetype or "audio/wav")},
        timeout=int(config.get("ELEVENLABS_REQUEST_TIMEOUT", 120)),
    )
    _raise_for_api_error(response, "clone this voice")
    try:
        voice_id = response.json().get("voice_id")
    except ValueError as exc:
        raise ElevenLabsError("ElevenLabs returned an invalid voice-clone response.") from exc
    if not voice_id:
        raise ElevenLabsError("ElevenLabs did not return a voice ID for this recording.")
    return voice_id


def clone_voice(file_obj, filename, mimetype, config, profile_label=None, owner_name=None, profile_id=None):
    name = _voice_name(profile_label, owner_name, profile_id)
    description = (
        "A voice profile created by its owner in TeachAlike. "
        "Use only with the owner's permission."
    )
    return _clone_files(file_obj, filename, mimetype, config, name, description)


def clone_voice_from_url(reference_url, config, profile_label=None, owner_name=None, profile_id=None):
    """Clone a legacy TeachAlike voice profile that has no ElevenLabs ID yet."""
    if not reference_url:
        raise ElevenLabsError("The voice profile has no source recording available.")
    try:
        response = requests.get(reference_url, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ElevenLabsError("The private voice recording could not be downloaded.") from exc
    return clone_voice(
        response.content,
        "legacy-voice-sample",
        response.headers.get("Content-Type", "audio/wav"),
        config,
        profile_label,
        owner_name,
        profile_id,
    )
