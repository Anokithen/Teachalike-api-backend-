"""NVIDIA-hosted ASR for pronunciation recordings."""

import os
import shutil
import subprocess
import tempfile

import requests
from flask import current_app


DEFAULT_ASR_URL = (
    "https://1598d209-5e27-4d3c-8079-4751568b1081."
    "invocation.api.nvcf.nvidia.com/v1/audio/transcriptions"
)


class NvidiaSpeechError(Exception):
    """A safe, user-facing NVIDIA speech recognition error."""


def _api_key(config):
    key = str(
        config.get("NVIDIA_ASR_API_KEY")
        or config.get("NVIDIA_API_KEY")
        or config.get("NVAPI_KEY")
        or ""
    ).strip()
    if not key:
        raise NvidiaSpeechError(
            "NVIDIA speech recognition is not configured. Set NVIDIA_ASR_API_KEY on the API server."
        )
    return key


def _error_message(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if detail:
                return str(detail)
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or response.reason)
            return str(payload.get("message") or response.reason)
    except (ValueError, AttributeError):
        pass
    return f"NVIDIA returned HTTP {response.status_code}."


def _ffmpeg_binary():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as err:
        raise NvidiaSpeechError(
            "ffmpeg is required to prepare pronunciation recordings."
        ) from err


def transcribe_audio(upload):
    """Convert a browser recording and transcribe it with NVIDIA ASR."""
    config = current_app.config
    source_path = output_path = None
    try:
        suffix = os.path.splitext(upload.filename or "recording.webm")[1] or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as source:
            source_path = source.name
            upload.save(source_path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output:
            output_path = output.name

        conversion = subprocess.run(
            [
                _ffmpeg_binary(),
                "-y",
                "-i",
                source_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                "-f",
                "wav",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if conversion.returncode != 0:
            raise NvidiaSpeechError(
                "The recording could not be prepared for NVIDIA speech recognition."
            )

        url = str(config.get("NVIDIA_ASR_API_URL", DEFAULT_ASR_URL)).strip() or DEFAULT_ASR_URL
        timeout = max(10, int(config.get("NVIDIA_ASR_REQUEST_TIMEOUT", 45)))
        with open(output_path, "rb") as audio_file:
            try:
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {_api_key(config)}",
                        # Offline file transcription returns one JSON response;
                        # event-stream is only appropriate for streaming clients.
                        "Accept": "application/json",
                    },
                    data={
                        "language": str(config.get("NVIDIA_ASR_LANGUAGE", "en-US")),
                        "response_format": "json",
                    },
                    files={"file": ("recording.wav", audio_file, "audio/wav")},
                    timeout=(10, timeout),
                )
            except requests.RequestException as err:
                raise NvidiaSpeechError(
                    "NVIDIA could not be reached for pronunciation recognition."
                ) from err

        if not response.ok:
            raise NvidiaSpeechError(
                f"NVIDIA could not transcribe the pronunciation recording: {_error_message(response)}"
            )

        try:
            payload = response.json()
        except ValueError as err:
            raise NvidiaSpeechError("NVIDIA returned an invalid transcription response.") from err

        if isinstance(payload, dict):
            transcript = str(payload.get("text") or payload.get("transcript") or "").strip()
        else:
            transcript = str(payload or "").strip()
        return transcript
    except FileNotFoundError as err:
        raise NvidiaSpeechError("The pronunciation recording could not be processed.") from err
    except subprocess.TimeoutExpired as err:
        raise NvidiaSpeechError(
            "That recording took too long to prepare. Please read one sentence at a time."
        ) from err
    finally:
        for path in (source_path, output_path):
            if path and os.path.exists(path):
                os.remove(path)
