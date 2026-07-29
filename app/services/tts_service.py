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
