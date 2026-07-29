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


def book_narration_public_id(
    owner_id,
    owner_name,
    book_id,
    book_title,
    voice_profile_id,
    generation_id,
):
    """Return a collision-safe canonical public ID for one narration generation.

    ``owner_name`` remains in the signature only for compatibility with older
    callers. Names never determine ownership or uniqueness.
    """
    del owner_name
    folder = get_generated_book_audio_folder(owner_id, book_id, book_title)
    return f"{folder}/voice_{int(voice_profile_id)}_{int(book_id)}_{int(generation_id)}"


def _cloudinary_modules():
    """Load Cloudinary only when a Cloudinary-backed operation is used.

    Cloudinary is an optional integration for the API's core routes. Keeping
    the import here allows the application to boot (and serve books,
    accounts, and reading sessions) when that integration is not installed or
    configured.
    """
    try:
        import cloudinary
        import cloudinary.api
        import cloudinary.exceptions
        import cloudinary.uploader
        import cloudinary.utils
    except ImportError as exc:
        raise RuntimeError(
            "Cloudinary support is not installed. Install the cloudinary package."
        ) from exc
    return cloudinary


def configure_cloudinary(config):
    """Configure the SDK or raise a sanitized configuration error."""
    cloudinary = _cloudinary_modules()
    values = {
        "cloud_name": config.get("CLOUDINARY_CLOUD_NAME"),
        "api_key": config.get("CLOUDINARY_API_KEY"),
        "api_secret": config.get("CLOUDINARY_API_SECRET"),
    }
    if not all(values.values()):
        raise CloudinaryServiceError(
            "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET on the API server."
        )
    cloudinary.config(**values, secure=True)
    return cloudinary


def _operation_config(config=None):
    if config is not None:
        return config
    return current_app.config if has_app_context() else {}


def _log_storage_failure(operation, public_id=None):
    if not has_app_context():
        return
    current_app.logger.error(
        "Cloudinary %s failed%s",
        operation,
        f" for public_id={public_id}" if public_id else "",
    )


def upload_asset(
    file,
    asset_folder,
    resource_type="auto",
    public_id=None,
    overwrite=False,
    delivery_type="upload",
    tags=None,
    context=None,
    config=None,
    **upload_options,
):
    """Upload an asset and return a normalized metadata dictionary.

    Asset routes use one common contract for images, video, and authenticated
    audio. Keeping the SDK call here also ensures provider errors never leak
    credentials or implementation details through an HTTP response.
    """
    try:
        operation_config = _operation_config(config)
        cloudinary = configure_cloudinary(operation_config)
        upload_kwargs = {
            "resource_type": resource_type,
            "asset_folder": asset_folder,
            "overwrite": overwrite,
            "type": delivery_type,
            "timeout": int(
                operation_config.get("CLOUDINARY_UPLOAD_TIMEOUT_SECONDS", 180)
            ),
            **upload_options,
        }
        if public_id:
            upload_kwargs["public_id"] = public_id
        if tags:
            upload_kwargs["tags"] = list(tags)
        if context:
            upload_kwargs["context"] = dict(context)
        if overwrite:
            upload_kwargs["invalidate"] = True
        result = cloudinary.uploader.upload(file, **upload_kwargs)
    except Exception as exc:
        if isinstance(exc, CloudinaryServiceError):
            raise
        _log_storage_failure("upload", public_id)
        raise CloudinaryUploadError("Cloudinary upload failed.") from exc

    metadata = {
        "asset_id": result.get("asset_id") or result.get("public_id"),
        "public_id": result.get("public_id"),
        "secure_url": result.get("secure_url"),
        "resource_type": result.get("resource_type") or resource_type,
        "delivery_type": result.get("type") or result.get("delivery_type") or delivery_type,
        "format": result.get("format"),
        "bytes": result.get("bytes"),
        "width": result.get("width"),
        "height": result.get("height"),
        "duration": result.get("duration"),
        "asset_folder": result.get("asset_folder") or asset_folder,
        "original_filename": result.get("original_filename") or getattr(file, "filename", None),
    }
    if not metadata["public_id"] or not metadata["secure_url"]:
        raise CloudinaryUploadError("Cloudinary upload returned incomplete metadata.")
    return metadata


def replace_asset(file, asset_folder, resource_type, public_id, **kwargs):
    """Replace a deterministic Cloudinary asset and invalidate cached bytes."""
    return upload_asset(
        file,
        asset_folder,
        resource_type=resource_type,
        public_id=public_id,
        overwrite=True,
        **kwargs,
    )


def delete_asset(
    public_id,
    resource_type,
    delivery_type="upload",
    config=None,
):
    """Delete an asset; missing Cloudinary resources are safely idempotent."""
    if not public_id:
        return {"result": "not found", "public_id": public_id}
    try:
        cloudinary = configure_cloudinary(_operation_config(config))
        result = cloudinary.uploader.destroy(
            public_id,
            resource_type=resource_type,
            type=delivery_type,
            invalidate=True,
        )
    except Exception as exc:
        if isinstance(exc, CloudinaryServiceError):
            raise
        _log_storage_failure("deletion", public_id)
        raise CloudinaryServiceError("Cloudinary deletion failed.") from exc
    status = result.get("result") if isinstance(result, dict) else result
    return {"result": status or "unknown", "public_id": public_id}


def get_asset_metadata(
    public_id,
    resource_type="image",
    delivery_type="upload",
    config=None,
):
    """Read normalized metadata for an exact server-owned Cloudinary public ID."""
    try:
        cloudinary = configure_cloudinary(_operation_config(config))
        result = cloudinary.api.resource(
            public_id,
            resource_type=resource_type,
            type=delivery_type,
        )
    except Exception as exc:
        if isinstance(exc, CloudinaryServiceError):
            raise
        _log_storage_failure("metadata lookup", public_id)
        raise CloudinaryServiceError("Cloudinary metadata lookup failed.") from exc
    return {
        "asset_id": result.get("asset_id") or result.get("public_id"),
        "public_id": result.get("public_id"),
        "secure_url": result.get("secure_url"),
        "resource_type": result.get("resource_type") or resource_type,
        "delivery_type": result.get("type") or delivery_type,
        "format": result.get("format"),
        "bytes": result.get("bytes"),
        "width": result.get("width"),
        "height": result.get("height"),
        "duration": result.get("duration"),
        "asset_folder": result.get("asset_folder"),
        "original_filename": result.get("original_filename"),
    }


def upload_voice_sample(
    file,
    owner_id,
    config,
    owner_name=None,
    voice_profile_id=None,
):
    """Compatibility wrapper over :func:`upload_asset` for private voice audio."""
    del owner_name
    if voice_profile_id is None:
        raise CloudinaryServiceError(
            "voice_profile_id is required for canonical voice-profile storage."
        )
    extension = validate_uploaded_file(file, "audio")
    folder = get_voice_profile_folder(owner_id)
    metadata = upload_asset(
        file,
        folder,
        resource_type="video",
        delivery_type="authenticated",
        public_id=f"{folder}/voice_profile_{int(voice_profile_id)}",
        overwrite=False,
        format=extension,
        config=config,
    )
    return metadata["secure_url"], metadata["public_id"]


def upload_book_narration(
    file,
    owner_id,
    owner_name,
    book_id,
    book_title,
    voice_profile_id,
    config,
    generation_id=None,
    return_metadata=False,
):
    """Compatibility wrapper for canonical private narration uploads."""
    if generation_id is None:
        raise CloudinaryServiceError(
            "generation_id is required for canonical narration storage."
        )
    folder = get_generated_book_audio_folder(owner_id, book_id, book_title)
    public_id = book_narration_public_id(
        owner_id,
        owner_name,
        book_id,
        book_title,
        voice_profile_id,
        generation_id,
    )
    metadata = upload_asset(
        file,
        folder,
        resource_type="video",
        delivery_type="authenticated",
        public_id=public_id,
        overwrite=False,
        format="mp3",
        config=config,
    )
    if return_metadata:
        return metadata
    return metadata["secure_url"], metadata["public_id"]


def signed_narration_delivery_url(public_id, fallback_url, config):
    """Narrations use the same authenticated delivery policy as voice samples."""
    return signed_voice_delivery_url(public_id, fallback_url, config)


def delete_authenticated_audio(public_id, config):
    """Compatibility wrapper for exact authenticated-audio deletion."""
    return delete_asset(
        public_id,
        resource_type="video",
        delivery_type="authenticated",
        config=config,
    )


def delete_voice_sample(public_id, config):
    """Delete a private voice sample stored as an authenticated video asset."""
    return delete_authenticated_audio(public_id, config)


def signed_voice_delivery_url(public_id, fallback_url, config):
    """Create a short signed authenticated-delivery URL after app authorization."""
    if not public_id:
        return fallback_url
    cloudinary = _cloudinary_modules()
    configure_cloudinary(config)
    # The stored public ID intentionally has no extension, while Cloudinary's
    # authenticated video/audio delivery URL must include the uploaded format.
    # Without it Cloudinary returns an unsigned-looking 404 response; browsers
    # following our cross-origin redirect surface that response as a generic
    # Axios "Network Error".
    path = urlparse(str(fallback_url or "")).path
    extension = path.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
    delivery_options = (
        {"format": extension}
        if extension in ALLOWED_EXTENSIONS
        else {}
    )
    url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        resource_type="video",
        type="authenticated",
        sign_url=True,
        secure=True,
        **delivery_options,
    )
    return url


def stream_authenticated_audio(
    public_id,
    fallback_url,
    config,
    range_header=None,
):
    """Proxy private Cloudinary audio without a browser cross-origin redirect."""
    signed_url = signed_voice_delivery_url(public_id, fallback_url, config)
    request_headers = {}
    if range_header:
        request_headers["Range"] = range_header
    timeout = int(config.get("CLOUDINARY_DELIVERY_TIMEOUT_SECONDS", 60))
    try:
        upstream = requests.get(
            signed_url,
            headers=request_headers,
            stream=True,
            allow_redirects=True,
            timeout=(5, timeout),
        )
    except requests.RequestException as exc:
        _log_storage_failure("authenticated audio delivery", public_id)
        raise CloudinaryServiceError(
            "Private audio delivery is temporarily unavailable."
        ) from exc

    if upstream.status_code not in {200, 206}:
        upstream.close()
        if has_app_context():
            current_app.logger.error(
                "Cloudinary audio delivery failed for public_id=%s status=%s",
                public_id,
                upstream.status_code,
            )
        raise CloudinaryServiceError(
            "Private audio delivery is temporarily unavailable."
        )

    response = Response(
        stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
        status=upstream.status_code,
        content_type=upstream.headers.get(
            "Content-Type",
            "application/octet-stream",
        ),
    )
    for header in (
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "Content-Disposition",
        "ETag",
        "Last-Modified",
    ):
        value = upstream.headers.get(header)
        if value:
            response.headers[header] = value
    response.call_on_close(upstream.close)
    return response


def upload_book_media(file, media_type, owner_id, config):
    """Compatibility wrapper for the legacy pre-book catalog image uploader."""
    extension = validate_uploaded_file(file, media_type)
    folder = f"{get_user_root_folder(owner_id)}/Image/Book_media"
    stem = sanitize_folder_segment(
        str(getattr(file, "filename", "")).rsplit(".", 1)[0]
    )
    metadata = upload_asset(
        file,
        folder,
        resource_type="image" if media_type == "image" else "video",
        public_id=f"{folder}/{stem}_{uuid4().hex}",
        overwrite=False,
        format=extension,
        config=config,
    )
    return metadata["secure_url"]


def upload_profile_image(
    file,
    profile_type,
    profile_id,
    config,
    *,
    owner_id=None,
    profile_name=None,
):
    """Compatibility wrapper for canonical account and child profile images."""
    extension = validate_uploaded_file(file, "image")
    if profile_type == "accounts":
        folder = get_user_profile_folder(owner_id or profile_id)
    elif profile_type == "children" and owner_id is not None:
        folder = get_child_profile_folder(
            owner_id,
            profile_id,
            profile_name,
        )
    else:
        raise CloudinaryServiceError(
            "owner_id is required for canonical child profile storage."
        )
    metadata = replace_asset(
        file,
        folder,
        resource_type="image",
        public_id=f"{folder}/profile",
        format=extension,
        config=config,
    )
    return metadata["secure_url"], metadata["public_id"]


def delete_profile_image(public_id, config):
    """Compatibility wrapper for exact public profile-image deletion."""
    return delete_asset(
        public_id,
        resource_type="image",
        delivery_type="upload",
        config=config,
    )
