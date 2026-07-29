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


@lru_cache(maxsize=2)
def _load_model(model_name, device, cache_dir):
    """Load and retain the expensive model once per web-process configuration."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        os.environ.setdefault("TTS_HOME", cache_dir)
        # Numba (used transitively by librosa) otherwise tries to cache beside
        # its installed package, which may be read-only in containers.
        numba_cache_dir = os.path.join(cache_dir, "numba-cache")
        os.makedirs(numba_cache_dir, exist_ok=True)
        os.environ.setdefault("NUMBA_CACHE_DIR", numba_cache_dir)
        matplotlib_cache_dir = os.path.join(cache_dir, "matplotlib-cache")
        os.makedirs(matplotlib_cache_dir, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", matplotlib_cache_dir)
        from TTS.api import TTS  # Keep Flask bootable until narration is requested.

        model = TTS(model_name=model_name, progress_bar=False)
        return model.to(device) if device else model
    except ImportError as exc:
        raise TTSError("Coqui TTS is not installed. Install the API requirements and redeploy.") from exc
    except Exception as exc:
        raise TTSError("Coqui model files could not be loaded. Check TTS model settings and server storage.") from exc


def _combine_wav_files(paths, destination):
    try:
        with wave.open(str(paths[0]), "rb") as first:
            params = first.getparams()
            frames = [first.readframes(first.getnframes())]
        for path in paths[1:]:
            with wave.open(str(path), "rb") as part:
                if (part.getnchannels(), part.getsampwidth(), part.getframerate()) != (params.nchannels, params.sampwidth, params.framerate):
                    raise TTSError("XTTS returned incompatible audio chunks.")
                frames.append(part.readframes(part.getnframes()))
        with wave.open(str(destination), "wb") as output:
            output.setparams(params)
            for frame_data in frames:
                output.writeframes(frame_data)
    except (wave.Error, OSError) as exc:
        raise TTSError("XTTS generated audio that could not be combined.") from exc


def _synthesize_chunk(model, chunk, reference_wav, chunk_path, config):
    """Synthesize one chunk with native cloning or Coqui's VC helper."""
    method = str(config.get("TTS_VOICE_CLONING_METHOD", "native")).lower()
    if method == "vc":
        # This is Coqui's equivalent of:
        # tts.tts_with_vc_to_file(text, speaker_wav="speaker.wav", file_path="output.wav")
        # It lets a standard TTS model speak first, then converts it to the
        # uploaded Cloudinary reference voice.
        model.tts_with_vc_to_file(
            text=chunk,
            speaker_wav=str(reference_wav),
            file_path=str(chunk_path),
        )
        return
    if method != "native":
        raise TTSError("TTS_VOICE_CLONING_METHOD must be either 'native' or 'vc'.")
    model.tts_to_file(
        text=chunk,
        speaker_wav=str(reference_wav),
        language=config.get("TTS_LANGUAGE", "en"),
        file_path=str(chunk_path),
    )


def synthesize_narration(text, reference_voice_url, output_path, config):
    """Generate a WAV narration using a signed Cloudinary reference-audio URL."""
    chunks = split_text_into_chunks(text, int(config.get("TTS_MAX_CHARS_PER_CHUNK", 280)))
    if not chunks:
        raise TTSError("This book has no text available for narration.")

    with tempfile.TemporaryDirectory(prefix="teachalike-xtts-") as temp_dir:
        temp_path = Path(temp_dir)
        downloaded_voice = temp_path / "reference-audio"
        reference_wav = temp_path / "reference.wav"
        _download_reference_voice(reference_voice_url, downloaded_voice)
        _to_wav(downloaded_voice, reference_wav)
        model = _load_model(
            config.get("TTS_MODEL_NAME"),
            config.get("TTS_DEVICE", "cpu"),
            config.get("TTS_CACHE_DIR"),
        )
        chunk_paths = []
        try:
            for index, chunk in enumerate(chunks):
                chunk_path = temp_path / f"chunk-{index}.wav"
                _synthesize_chunk(model, chunk, reference_wav, chunk_path, config)
                chunk_paths.append(chunk_path)
            _combine_wav_files(chunk_paths, output_path)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError("Coqui could not generate this narration. Try a clearer voice sample, compatible model, or shorter book.") from exc
