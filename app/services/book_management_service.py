"""Shared validation and cleanup helpers for admin and teacher book workflows."""

import json

from flask import current_app

from app.models.asset_model import Asset
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    validate_upload_size,
    validate_uploaded_file,
)
from app.validators import MAX_URL_LENGTH, is_safe_http_url

MAX_ILLUSTRATIONS = 8


def request_book_data(request):
    """Return normalized JSON/form values without accepting ownership fields."""
    if request.is_json:
        return request.get_json(silent=True) or {}
    data = request.form.to_dict()
    raw_images = data.get("image_urls")
    if raw_images:
        try:
            data["image_urls"] = json.loads(raw_images)
        except (TypeError, ValueError):
            data["image_urls"] = raw_images
    return data


def validate_book_payload(data):
    errors = []
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    age_group = str(data.get("age_group", "")).strip()
    reading_level = str(data.get("reading_level", "")).strip().lower()
    image_urls = data.get("image_urls") or []
    if not title:
        errors.append("title is required.")
    elif len(title) > 200:
        errors.append("title must be 200 characters or fewer.")
    if len(description) > 5000:
        errors.append("description must be 5000 characters or fewer.")
    if not age_group:
        errors.append("age_group is required.")
    elif len(age_group) > 50:
        errors.append("age_group must be 50 characters or fewer.")
    if reading_level not in {"beginner", "intermediate", "advanced"}:
        errors.append("reading_level must be beginner, intermediate, or advanced.")
    if not isinstance(image_urls, list) or len(image_urls) > MAX_ILLUSTRATIONS or any(
        not isinstance(url, str) or not is_safe_http_url(url) for url in image_urls
    ):
        errors.append(
            f"image_urls must contain up to {MAX_ILLUSTRATIONS} valid HTTPS URLs "
            f"(or local HTTP URLs) of {MAX_URL_LENGTH} characters or fewer."
        )
    urls = {
        name: str(data.get(name, "")).strip()
        for name in ("content_url", "cover_image_url", "video_url")
    }
    for name, value in urls.items():
        if value and not is_safe_http_url(value):
            errors.append(
                f"{name} must be a valid HTTPS URL (or local HTTP URL) of "
                f"{MAX_URL_LENGTH} characters or fewer."
            )
    values = {
        "title": title,
        "description": description or None,
        "age_group": age_group,
        "reading_level": reading_level,
        "text_content": str(data.get("text_content", "")).strip() or None,
        "content_url": urls["content_url"] or None,
        "cover_image_url": urls["cover_image_url"] or None,
        "video_url": urls["video_url"] or None,
        "image_urls": [url.strip() for url in image_urls]
        if isinstance(image_urls, list)
        else [],
    }
    return errors, values


def validate_media_files(request):
    cover = request.files.get("cover_image")
    illustrations = request.files.getlist("illustrations")
    video = request.files.get("video")
    illustrations = [item for item in illustrations if item and item.filename]
    if len(illustrations) > MAX_ILLUSTRATIONS:
        raise ValueError(f"A book may have at most {MAX_ILLUSTRATIONS} illustrations.")
    for upload in ([cover] if cover and cover.filename else []) + illustrations:
        validate_uploaded_file(upload, "image")
        validate_upload_size(upload, current_app.config["MAX_PROFILE_IMAGE_SIZE_MB"])
        upload.stream.seek(0)
    if video and video.filename:
        validate_uploaded_file(video, "video")
        validate_upload_size(video, current_app.config["MAX_BOOK_VIDEO_SIZE_MB"])
        video.stream.seek(0)
    else:
        video = None
    return (cover if cover and cover.filename else None), illustrations, video


def asset_reference(asset):
    return {
        "public_id": asset.cloudinary_public_id,
        "resource_type": asset.cloudinary_resource_type,
        "delivery_type": asset.cloudinary_delivery_type,
    }


def cleanup_references(references):
    """Best-effort cleanup after the database is in a consistent state."""
    for reference in references:
        try:
            delete_asset(
                reference.get("public_id"),
                reference.get("resource_type") or "auto",
                reference.get("delivery_type") or "upload",
            )
        except CloudinaryServiceError:
            current_app.logger.error(
                "Book media cleanup failed for public_id=%s",
                reference.get("public_id"),
            )


def book_asset_references(book_id):
    return [asset_reference(asset) for asset in Asset.query.filter_by(book_id=book_id).all()]
