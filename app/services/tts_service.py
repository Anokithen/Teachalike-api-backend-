"""Coqui TTS/voice-conversion helpers for cached private book narrations."""
import os
import re
import shutil
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

class TTSError(Exception):
    """A Coqui TTS setup, download, or inference error safe to show to API users."""


def split_text_into_chunks(text, max_chars=280):
    """Keep XTTS input bounded while preserving sentence-sized narration."""
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", text or "") if item.strip()]
    chunks, current = [], ""
    for sentence in sentences:
        # Split unusually long sentences at word boundaries.
        pieces = []
        words, piece = sentence.split(), ""
        for word in words:
            candidate = f"{piece} {word}".strip()
            if piece and len(candidate) > max_chars:
                pieces.append(piece)
                piece = word
            else:
                piece = candidate
        if piece:
            pieces.append(piece)
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _download_reference_voice(url, destination):
    try:
        import requests
    except ImportError as exc:
        raise TTSError(
            "The requests package is not installed for narration generation."
        ) from exc
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        Path(destination).write_bytes(response.content)
    except requests.RequestException as exc:
        raise TTSError("The private reference voice recording could not be downloaded.") from exc


def _ffmpeg_binary():
    """Prefer an explicitly configured/system ffmpeg, then imageio's bundled one."""
    configured = os.getenv("FFMPEG_BINARY")
    if configured:
        return configured
    system_binary = shutil.which("ffmpeg")
    if system_binary:
        return system_binary
    try:
        import imageio_ffmpeg

        bundled_binary = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled_binary and os.path.exists(bundled_binary):
            return bundled_binary
    except (ImportError, RuntimeError):
        pass
    raise TTSError(
        "ffmpeg is required for XTTS narration generation. Install ffmpeg or set FFMPEG_BINARY."
    )


def _to_wav(source, destination):
    try:
        subprocess.run(
            [_ffmpeg_binary(), "-y", "-i", str(source), "-ac", "1", "-ar", "22050", str(destination)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise TTSError("The configured ffmpeg binary could not be started. Check FFMPEG_BINARY.") from exc
    except subprocess.CalledProcessError as exc:
        raise TTSError("The reference voice recording could not be converted for XTTS.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSError("The reference voice recording took too long to convert.") from exc
