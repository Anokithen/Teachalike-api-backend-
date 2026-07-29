"""Offline speech-to-text helpers backed by a local Vosk model."""

import json
import os
import shutil
import subprocess
import tempfile
import wave

from flask import current_app

_model = None
_model_path = None


class SpeechRecognitionError(Exception):
    """A safe, user-facing speech recognition configuration or audio error."""


def _get_model():
    global _model, _model_path
    path = current_app.config.get("VOSK_MODEL_PATH")
    if not path or not os.path.isdir(path):
        raise SpeechRecognitionError(
            "Offline speech recognition is not configured. Set VOSK_MODEL_PATH to a downloaded English Vosk model."
        )
    if _model is None or _model_path != path:
        try:
            from vosk import Model
        except ImportError as err:
            raise SpeechRecognitionError("The Python Vosk package is not installed on the server.") from err
        _model = Model(path)
        _model_path = path
    return _model
