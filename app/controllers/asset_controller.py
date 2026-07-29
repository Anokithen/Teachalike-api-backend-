"""Authenticated asset upload, query, and deletion workflows."""

from pathlib import Path
from uuid import uuid4

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.middleware import can_access_child
from app.models.asset_model import (
    Asset,
    BOOK_VIDEO,
    CHILD_PROFILE_IMAGE,
    GENERATED_BOOK_AUDIO,
    STATUS_COMPLETED,
    STATUS_CLEANUP_FAILED,
    STATUS_DELETED,
    USER_PROFILE_IMAGE,
    VOICE_PROFILE,
)
from app.models.book_model import Book
from app.models.book_narration_model import BookNarration, STATUS_READY
from app.models.child_model import Child
from app.models.parent_model import Parent
from app.models.voice_profile_model import VoiceProfile
from app.services.cloudinary_path_service import (
    get_book_video_folder,
    get_child_profile_folder,
    get_generated_book_audio_folder,
    get_user_profile_folder,
    sanitize_folder_segment,
)
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    upload_asset,
    validate_upload_size,
    validate_uploaded_file,
)
from app.utils import utc_now
from app.services.elevenlabs_service import delete_voice

SIZE_CONFIG = {
    USER_PROFILE_IMAGE: "MAX_PROFILE_IMAGE_SIZE_MB",
    CHILD_PROFILE_IMAGE: "MAX_CHILD_IMAGE_SIZE_MB",
    VOICE_PROFILE: "MAX_VOICE_PROFILE_SIZE_MB",
    GENERATED_BOOK_AUDIO: "MAX_BOOK_AUDIO_SIZE_MB",
    BOOK_VIDEO: "MAX_BOOK_VIDEO_SIZE_MB",
}


def _response(message, data=None, status=200):
    return jsonify({"success": True, "message": message, "data": data}), status


def _error(message, status):
    return jsonify({"success": False, "message": message, "data": None}), status


def _legacy_error(result):
    response, status = result
    payload = response.get_json(silent=True) or {}
    return jsonify({"error": payload.get("message") or "Asset operation failed."}), status


def _validated_file(category, media_type):
    upload = request.files.get("file") or request.files.get("profile_image") or request.files.get("audio")
    if upload is None or not upload.filename:
        return None, _error("A file is required.", 400)
    try:
        validate_uploaded_file(upload, media_type)
    except ValueError as exc:
        return None, _error(str(exc), 415)
    limit_mb = current_app.config[SIZE_CONFIG[category]]
    try:
        validate_upload_size(upload, limit_mb)
    except ValueError:
        return None, _error(f"The file exceeds the {limit_mb} MB limit.", 413)
    upload.stream.seek(0)
    return upload, None


def _new_asset(metadata, category, owner_id, **relations):
    return Asset.from_cloudinary_metadata(
        metadata,
        category=category,
        owner_user_id=owner_id,
        active_slot=relations.pop("active_slot", None),
        status=relations.pop("status", STATUS_COMPLETED),
        **relations,
    )


def _cleanup_upload(metadata):
    try:
        delete_asset(
            metadata["public_id"],
            metadata["resource_type"],
            metadata.get("delivery_type") or "upload",
        )
    except CloudinaryServiceError:
        current_app.logger.error(
            "Orphan asset cleanup failed for Cloudinary asset_id=%s",
            metadata.get("asset_id"),
        )


def _save_profile(upload, category, folder, owner_id, child=None):
    existing_query = Asset.query.filter_by(
        child_id=child.id if child else None,
        asset_category=category,
        deleted_at=None,
    )
    if child is None:
        existing_query = existing_query.filter_by(owner_user_id=owner_id)
    existing = existing_query.first()
    metadata = upload_asset(
        upload,
        folder,
        resource_type="image",
        # Cloudinary public IDs are account-wide even in dynamic-folder mode.
        # Prefix with the trusted folder while keeping the deterministic
        # filename requested by the storage contract.
        public_id=f"{folder}/profile",
        overwrite=True,
        tags=[category.lower()],
    )
    asset = _new_asset(
        metadata,
        category,
        owner_id,
        child_id=child.id if child else None,
        active_slot=(
            f"child:{child.id}:profile"
            if child
            else f"user:{owner_id}:profile"
        ),
    )
    try:
        db.session.add(asset)
        if child:
            child.profile_image_url = metadata["secure_url"]
            child.profile_image_public_id = metadata["public_id"]
        else:
            current_user.profile_image_url = metadata["secure_url"]
            current_user.profile_image_public_id = metadata["public_id"]
        if existing:
            existing.status = STATUS_DELETED
            existing.deleted_at = utc_now()
            existing.active_slot = None
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        if existing:
            # A deterministic Cloudinary overwrite has already replaced the
            # old bytes. Deleting it here would leave both the old DB row and
            # related profile with no deliverable asset. Keep the confirmed
            # replacement for a safe metadata retry.
            current_app.logger.error(
                "Profile metadata save failed after Cloudinary replacement asset_id=%s",
                metadata.get("asset_id"),
            )
        else:
            _cleanup_upload(metadata)
        raise
    if existing and existing.cloudinary_public_id != metadata["public_id"]:
        try:
            delete_asset(
                existing.cloudinary_public_id,
                existing.cloudinary_resource_type,
                existing.cloudinary_delivery_type,
            )
        except CloudinaryServiceError:
            current_app.logger.error(
                "Previous profile cleanup failed for Cloudinary asset_id=%s",
                existing.cloudinary_asset_id,
            )
            existing.status = STATUS_CLEANUP_FAILED
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.error(
                    "Could not record cleanup failure for asset_id=%s",
                    existing.id,
                )
    return asset
