"""Centralized server-side Cloudinary storage and delivery helpers."""
from urllib.parse import urlparse
from uuid import uuid4

import requests
from flask import Response, current_app, has_app_context, stream_with_context

from app.services.cloudinary_path_service import (
    get_child_profile_folder,
    get_generated_book_audio_folder,
    get_user_root_folder,
    get_user_profile_folder,
    get_voice_profile_folder,
    sanitize_folder_segment,
)


class CloudinaryServiceError(RuntimeError):
    """Safe application-level error for Cloudinary upload/delete failures."""


class CloudinaryUploadError(CloudinaryServiceError):
    """Raised when Cloudinary cannot store an uploaded asset."""

# Browsers commonly record as WebM, OGG, or M4A rather than MP3/WAV.
# Cloudinary stores all of these as authenticated video/audio resources.
ALLOWED_EXTENSIONS = {"mp3", "wav", "webm", "ogg", "m4a", "mp4"}
ALLOWED_MIME_TYPES = {
    "image": {
        "jpg": {"image/jpeg"},
        "jpeg": {"image/jpeg"},
        "png": {"image/png"},
        "webp": {"image/webp"},
    },
    "video": {
        "mp4": {"video/mp4"},
        "webm": {"video/webm"},
        "mov": {"video/quicktime"},
    },
    "audio": {
        # Browser and operating-system MIME databases are inconsistent for
        # MP3 files. Content is still checked using magic bytes below.
        "mp3": {
            "audio/mpeg",
            "audio/mp3",
            "audio/x-mpeg",
            "audio/x-mp3",
            "audio/mpeg3",
            "audio/x-mpeg3",
        },
        "wav": {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"},
        "webm": {"audio/webm"},
        "ogg": {"audio/ogg"},
        "m4a": {"audio/mp4", "audio/x-m4a"},
        "mp4": {"audio/mp4", "video/mp4"},
    },
}


def validate_uploaded_file(file, media_type):
    """Require a supported extension, MIME type, and matching file signature."""
    if media_type not in ALLOWED_MIME_TYPES:
        raise ValueError("Unsupported upload type.")
    filename = str(getattr(file, "filename", "") or "")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_type = str(getattr(file, "mimetype", "") or "").split(";", 1)[0].strip().lower()
    allowed = ALLOWED_MIME_TYPES[media_type]
    if extension not in allowed:
        formats = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported {media_type} format. Allowed formats: {formats}.")
    if not _has_expected_signature(file, media_type, extension):
        raise ValueError(f"The uploaded file contents do not match the .{extension} format.")

    # Some browsers send locally selected audio with no MIME type, which
    # becomes application/octet-stream in multipart form data. Accept that
    # generic type only when extension and magic bytes both identify audio.
    generic_audio_type = media_type == "audio" and mime_type in {
        "",
        "application/octet-stream",
    }
    if mime_type not in allowed[extension] and not generic_audio_type:
        expected = ", ".join(sorted(allowed[extension]))
        raise ValueError(f"The uploaded .{extension} file must have MIME type: {expected}.")
    return extension


def uploaded_file_size(file):
    """Return upload size without consuming or rewinding the caller's position."""
    stream = getattr(file, "stream", file)
    position = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(position)
    return size


def validate_upload_size(file, limit_mb):
    """Reject files larger than a configured positive megabyte limit."""
    limit_mb = int(limit_mb)
    if limit_mb <= 0:
        raise ValueError("Upload size limit must be a positive integer.")
    if uploaded_file_size(file) > limit_mb * 1024 * 1024:
        raise ValueError(f"The file exceeds the {limit_mb} MB limit.")


def _has_expected_signature(file, media_type, extension):
    """Check common media magic bytes while preserving the upload stream position."""
    stream = getattr(file, "stream", file)
    try:
        position = stream.tell()
        signature = stream.read(32)
        stream.seek(position)
    except (AttributeError, OSError):
        return False

    if media_type == "image":
        return {
            "jpg": signature.startswith(b"\xff\xd8\xff"),
            "jpeg": signature.startswith(b"\xff\xd8\xff"),
            "png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
            "webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
        }.get(extension, False)

    if media_type == "video":
        if extension == "webm":
            return signature.startswith(b"\x1a\x45\xdf\xa3")
        if len(signature) >= 12 and signature[4:8] == b"ftyp":
            return extension == "mp4" or signature[8:12] == b"qt  "
        return False

    if extension == "mp3":
        return signature.startswith(b"ID3") or (len(signature) >= 2 and signature[0] == 0xFF and signature[1] & 0xE0 == 0xE0)
    if extension == "wav":
        return signature.startswith(b"RIFF") and signature[8:12] == b"WAVE"
    if extension == "ogg":
        return signature.startswith(b"OggS")
    if extension == "webm":
        return signature.startswith(b"\x1a\x45\xdf\xa3")
    if extension in {"m4a", "mp4"}:
        return len(signature) >= 12 and signature[4:8] == b"ftyp"
    return False
