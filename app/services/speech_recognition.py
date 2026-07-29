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


def transcribe_audio(upload):
    """Convert a browser recording to WAV and recognise it entirely on this server."""
    model = _get_model()
    suffix = os.path.splitext(upload.filename or "recording.webm")[1] or ".webm"
    source_path = output_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as source:
            source_path = source.name
            upload.save(source_path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output:
            output_path = output.name

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except (ImportError, RuntimeError) as err:
                raise SpeechRecognitionError("ffmpeg is required for offline speech recognition but is not installed on the server.") from err
        conversion = subprocess.run(
            [ffmpeg, "-y", "-i", source_path, "-ar", "16000", "-ac", "1", "-f", "wav", output_path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if conversion.returncode != 0:
            raise SpeechRecognitionError("The recording could not be prepared for recognition. Please try again.")

        from vosk import KaldiRecognizer
        with wave.open(output_path, "rb") as audio:
            recognizer = KaldiRecognizer(model, audio.getframerate())
            while True:
                chunk = audio.readframes(4000)
                if not chunk:
                    break
                recognizer.AcceptWaveform(chunk)
            transcript = json.loads(recognizer.FinalResult()).get("text", "").strip()
        return transcript
    except FileNotFoundError as err:
        raise SpeechRecognitionError("The local audio converter could not be started.") from err
    except subprocess.TimeoutExpired as err:
        raise SpeechRecognitionError("That recording took too long to process. Please read one sentence at a time.") from err
    finally:
        for path in (source_path, output_path):
            if path and os.path.exists(path):
                os.remove(path)
