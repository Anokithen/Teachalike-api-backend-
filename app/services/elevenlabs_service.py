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


def delete_voice(voice_id, config):
    """Delete the upstream clone when a user deletes a local profile."""
    if not voice_id:
        return
    response = _request(
        "DELETE",
        f"{API_BASE_URL}/voices/{voice_id}",
        "delete this voice",
        headers={"xi-api-key": _api_key(config)},
        timeout=int(config.get("ELEVENLABS_REQUEST_TIMEOUT", 120)),
    )
    if response.status_code == 404:
        return
    _raise_for_api_error(response, "delete this voice")


def _ffmpeg_binary(config):
    configured = config.get("FFMPEG_BINARY") or os.getenv("FFMPEG_BINARY")
    binary = configured or shutil.which("ffmpeg")
    if not binary:
        raise ElevenLabsError("ffmpeg is required to combine a long narration. Install ffmpeg on the API server.")
    return binary


def split_text_into_chunks(text, max_chars=4500):
    """Split a book into API-sized, sentence-aware chunks."""
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", text or "") if item.strip()]
    chunks, current = [], ""
    for sentence in sentences:
        pieces, words, piece = [], sentence.split(), ""
        for word in words:
            candidate = f"{piece} {word}".strip()
            if piece and len(candidate) > max_chars:
                pieces.append(piece)
                piece = word
            else:
                piece = candidate
        if piece:
            pieces.append(piece)
        for item in pieces:
            candidate = f"{current} {item}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = item
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _synthesize_chunk(voice_id, text, config, previous_text=None, next_text=None):
    payload = {
        "text": text,
        "model_id": config.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
    }
    language_code = str(config.get("ELEVENLABS_LANGUAGE_CODE") or "").strip()
    if language_code:
        payload["language_code"] = language_code
    if previous_text:
        payload["previous_text"] = previous_text
    if next_text:
        payload["next_text"] = next_text

    response = _request(
        "POST",
        f"{API_BASE_URL}/text-to-speech/{voice_id}",
        "generate this narration",
        params={"output_format": config.get("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")},
        headers={"xi-api-key": _api_key(config), "Content-Type": "application/json"},
        json=payload,
        timeout=int(config.get("ELEVENLABS_REQUEST_TIMEOUT", 120)),
    )
    _raise_for_api_error(response, "generate this narration")
    if not response.content:
        raise ElevenLabsError("ElevenLabs returned an empty audio file.")
    return response.content


def _combine_mp3_files(paths, destination, config):
    """Concatenate MP3 chunks with ffmpeg so browser playback stays reliable."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as manifest:
        manifest_path = manifest.name
        for path in paths:
            safe_path = str(Path(path).resolve()).replace("'", "'\\''")
            manifest.write(f"file '{safe_path}'\n")
    try:
        subprocess.run(
            [_ffmpeg_binary(config), "-y", "-f", "concat", "-safe", "0", "-i", manifest_path, "-c", "copy", str(destination)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=max(30, int(config.get("ELEVENLABS_REQUEST_TIMEOUT", 120))),
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise ElevenLabsError("The generated narration chunks could not be combined.") from exc
    finally:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)


def synthesize_narration(text, voice_id, output_path, config):
    """Generate a complete MP3 narration from an ElevenLabs voice clone."""
    max_chars = max(100, int(config.get("ELEVENLABS_MAX_CHARS_PER_CHUNK", 4500)))
    chunks = split_text_into_chunks(text, max_chars)
    if not chunks:
        raise ElevenLabsError("This book has no text available for narration.")

    with tempfile.TemporaryDirectory(prefix="teachalike-elevenlabs-") as temp_dir:
        chunk_paths = []
        for index, chunk in enumerate(chunks):
            audio = _synthesize_chunk(
                voice_id,
                chunk,
                config,
                previous_text=chunks[index - 1][-500:] if index else None,
                next_text=chunks[index + 1][:500] if index + 1 < len(chunks) else None,
            )
            chunk_path = Path(temp_dir) / f"chunk-{index}.mp3"
            chunk_path.write_bytes(audio)
            chunk_paths.append(chunk_path)
        _combine_mp3_files(chunk_paths, output_path, config)
