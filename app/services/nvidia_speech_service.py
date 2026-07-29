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
