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


def upload_user_profile_image(legacy_response=False):
    """Upload or replace the authenticated account's profile image."""
    upload, error = _validated_file(USER_PROFILE_IMAGE, "image")
    if error:
        return _legacy_error(error) if legacy_response else error
    try:
        asset = _save_profile(
            upload,
            USER_PROFILE_IMAGE,
            get_user_profile_folder(current_user.id),
            current_user.id,
        )
        if legacy_response:
            return jsonify({
                "message": "Profile image updated successfully.",
                "parent": current_user.to_dict(),
            }), 200
        return _response("Profile image uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        if legacy_response:
            return jsonify({"error": "Profile image upload failed."}), 503
        return _error("Profile image upload failed.", 503)
    except SQLAlchemyError:
        if legacy_response:
            return jsonify({"error": "Profile image metadata could not be saved."}), 500
        return _error("Profile image metadata could not be saved.", 500)


def upload_child_profile_image(child_id, legacy_response=False):
    """Upload or replace a profile image for a managed child."""
    child = db.session.get(Child, child_id)
    if child is None:
        result = _error("Child not found.", 404)
        return _legacy_error(result) if legacy_response else result
    if not can_access_child(child):
        result = _error("You cannot manage this child.", 403)
        return _legacy_error(result) if legacy_response else result
    upload, error = _validated_file(CHILD_PROFILE_IMAGE, "image")
    if error:
        return _legacy_error(error) if legacy_response else error
    try:
        asset = _save_profile(
            upload,
            CHILD_PROFILE_IMAGE,
            get_child_profile_folder(current_user.id, child.id, child.name),
            current_user.id,
            child,
        )
        if legacy_response:
            return jsonify({
                "message": "Child profile image updated successfully.",
                "child": child.to_dict(),
            }), 200
        return _response("Child profile image uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        if legacy_response:
            return jsonify({"error": "Child profile image upload failed."}), 503
        return _error("Child profile image upload failed.", 503)
    except SQLAlchemyError:
        if legacy_response:
            return jsonify({"error": "Child profile metadata could not be saved."}), 500
        return _error("Child profile metadata could not be saved.", 500)


def delete_user_profile_image_legacy():
    """Remove the current profile image through the asset ledger when present."""
    asset = Asset.query.filter_by(
        owner_user_id=current_user.id,
        asset_category=USER_PROFILE_IMAGE,
        deleted_at=None,
    ).order_by(Asset.id.desc()).first()
    if asset:
        result = delete_stored_asset(asset.id)
        response, status = result
        if status >= 400:
            return _legacy_error(result)
        return jsonify({
            "message": "Profile image removed.",
            "parent": current_user.to_dict(),
        }), 200

    public_id = current_user.profile_image_public_id
    try:
        if public_id:
            delete_asset(public_id, "image", "upload")
        current_user.profile_image_url = None
        current_user.profile_image_public_id = None
        db.session.commit()
        return jsonify({
            "message": "Profile image removed.",
            "parent": current_user.to_dict(),
        }), 200
    except CloudinaryServiceError:
        return jsonify({"error": "Profile image removal failed."}), 503
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Profile image removal failed."}), 500


def delete_child_profile_image_legacy(child_id):
    """Remove a managed child's image while preserving the legacy response."""
    child = db.session.get(Child, child_id)
    if child is None or not can_access_child(child):
        return jsonify({"error": "Child not found."}), 404
    asset = Asset.query.filter_by(
        child_id=child.id,
        asset_category=CHILD_PROFILE_IMAGE,
        deleted_at=None,
    ).order_by(Asset.id.desc()).first()
    if asset:
        result = delete_stored_asset(asset.id)
        response, status = result
        if status >= 400:
            return _legacy_error(result)
        return jsonify({
            "message": "Child profile image removed.",
            "child": child.to_dict(),
        }), 200

    public_id = child.profile_image_public_id
    try:
        if public_id:
            delete_asset(public_id, "image", "upload")
        child.profile_image_url = None
        child.profile_image_public_id = None
        db.session.commit()
        return jsonify({
            "message": "Child profile image removed.",
            "child": child.to_dict(),
        }), 200
    except CloudinaryServiceError:
        return jsonify({"error": "Profile image removal failed."}), 503
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Profile image removal failed."}), 500


def upload_voice_profile():
    """Create a cloned voice through the shared legacy-compatible workflow."""
    from app.controllers.voice_profile_controller import create_voice_profile

    return create_voice_profile(asset_response=True)


def upload_book_narration(book_id):
    """Store a completed narration upload as a distinct generation."""
    book = db.session.get(Book, book_id)
    if book is None:
        return _error("Book not found.", 404)
    profile_id = request.form.get("voice_profile_id", type=int)
    profile = db.session.get(VoiceProfile, profile_id) if profile_id else None
    if profile is None:
        return _error("Voice profile not found.", 404)
    if not current_user.is_admin and profile.parent_id != current_user.id:
        return _error("You cannot use this voice profile.", 403)
    if profile.status != STATUS_READY:
        return _error("The selected voice profile is not ready.", 422)
    upload, error = _validated_file(GENERATED_BOOK_AUDIO, "audio")
    if error:
        return error
    narration = BookNarration(
        book_id=book.id, voice_profile_id=profile.id, status=STATUS_READY
    )
    metadata = None
    try:
        db.session.add(narration)
        db.session.flush()
        metadata = upload_asset(
            upload,
            get_generated_book_audio_folder(profile.parent_id, book.id, book.title),
            resource_type="video",
            public_id=(
                f"{get_generated_book_audio_folder(profile.parent_id, book.id, book.title)}"
                f"/voice_{profile.id}_{book.id}_{narration.id}"
            ),
            overwrite=False,
            delivery_type="authenticated",
        )
        narration.narration_audio_url = metadata["secure_url"]
        narration.cloudinary_public_id = metadata["public_id"]
        asset = _new_asset(
            metadata,
            GENERATED_BOOK_AUDIO,
            profile.parent_id,
            book_id=book.id,
            voice_profile_id=profile.id,
            generation_id=narration.id,
        )
        db.session.add(asset)
        db.session.commit()
        return _response("Book narration uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        db.session.rollback()
        return _error("Book narration upload failed.", 503)
    except SQLAlchemyError:
        db.session.rollback()
        if metadata:
            _cleanup_upload(metadata)
        return _error("Narration metadata could not be saved.", 500)


def upload_book_video(book_id):
    """Store an administrator-owned video for an existing book."""
    book = db.session.get(Book, book_id)
    if book is None:
        return _error("Book not found.", 404)
    upload, error = _validated_file(BOOK_VIDEO, "video")
    if error:
        return error
    metadata = None
    try:
        filename = sanitize_folder_segment(Path(upload.filename).stem)
        metadata = upload_asset(
            upload,
            get_book_video_folder(current_user.id, current_user.id, book.id, book.title),
            resource_type="video",
            public_id=f"{filename}_{uuid4().hex}",
            overwrite=False,
        )
        asset = _new_asset(
            metadata,
            BOOK_VIDEO,
            current_user.id,
            admin_id=current_user.id,
            book_id=book.id,
        )
        db.session.add(asset)
        book.video_url = metadata["secure_url"]
        db.session.commit()
        return _response("Book video uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        return _error("Book video upload failed.", 503)
    except SQLAlchemyError:
        db.session.rollback()
        if metadata:
            _cleanup_upload(metadata)
        return _error("Book video metadata could not be saved.", 500)


def _can_manage_asset(asset):
    if not asset:
        return False
    if current_user.is_admin or asset.owner_user_id == current_user.id:
        return True
    if asset.asset_category == CHILD_PROFILE_IMAGE and asset.child_id:
        return can_access_child(db.session.get(Child, asset.child_id))
    return False


def get_asset(asset_id):
    asset = db.session.get(Asset, asset_id)
    if not _can_manage_asset(asset) or asset.deleted_at is not None:
        return _error("Asset not found.", 404)
    return _response("Asset retrieved.", asset.to_dict())


def list_my_assets():
    assets = Asset.query.filter_by(
        owner_user_id=current_user.id, deleted_at=None
    ).order_by(Asset.id.desc()).all()
    return _response("Assets retrieved.", [asset.to_dict() for asset in assets])


def list_book_assets(book_id):
    if db.session.get(Book, book_id) is None:
        return _error("Book not found.", 404)
    query = Asset.query.filter_by(book_id=book_id, deleted_at=None)
    if not current_user.is_admin:
        query = query.filter_by(owner_user_id=current_user.id)
    return _response(
        "Book assets retrieved.",
        [asset.to_dict() for asset in query.order_by(Asset.id.desc()).all()],
    )


def delete_stored_asset(asset_id):
    asset = db.session.get(Asset, asset_id)
    if not _can_manage_asset(asset):
        return _error("Asset not found.", 404)
    if (
        asset.deleted_at is not None
        and asset.status != STATUS_CLEANUP_FAILED
    ):
        return _response("Asset was already deleted.", asset.to_dict())

    profile = None
    if asset.asset_category == VOICE_PROFILE and asset.voice_profile_id:
        profile = db.session.get(VoiceProfile, asset.voice_profile_id)
        if profile and profile.narrations:
            return _error("This voice profile is still used by narrations.", 422)
        if profile and profile.reading_sessions:
            return _error("This voice profile is still used by reading sessions.", 422)
    try:
        delete_asset(
            asset.cloudinary_public_id,
            asset.cloudinary_resource_type,
            asset.cloudinary_delivery_type,
        )
        asset.status = STATUS_DELETED
        asset.deleted_at = utc_now()
        asset.active_slot = None

        if asset.asset_category == USER_PROFILE_IMAGE:
            owner = db.session.get(Parent, asset.owner_user_id)
            if owner and owner.profile_image_public_id == asset.cloudinary_public_id:
                owner.profile_image_url = None
                owner.profile_image_public_id = None
        elif asset.asset_category == CHILD_PROFILE_IMAGE and asset.child_id:
            child = db.session.get(Child, asset.child_id)
            if child and child.profile_image_public_id == asset.cloudinary_public_id:
                child.profile_image_url = None
                child.profile_image_public_id = None
        elif asset.asset_category == VOICE_PROFILE and profile:
            delete_voice(profile.elevenlabs_voice_id, current_app.config)
            db.session.delete(profile)
        elif asset.asset_category == GENERATED_BOOK_AUDIO and asset.generation_id:
            generation = db.session.get(BookNarration, asset.generation_id)
            if generation:
                db.session.delete(generation)
        elif asset.asset_category == BOOK_VIDEO and asset.book_id:
            book = db.session.get(Book, asset.book_id)
            if book and book.video_url == asset.cloudinary_secure_url:
                replacement = (
                    Asset.query.filter(
                        Asset.book_id == book.id,
                        Asset.asset_category == BOOK_VIDEO,
                        Asset.deleted_at.is_(None),
                        Asset.id != asset.id,
                    )
                    .order_by(Asset.id.desc())
                    .first()
                )
                book.video_url = (
                    replacement.cloudinary_secure_url if replacement else None
                )
        db.session.commit()
        return _response("Asset deleted.", asset.to_dict())
    except CloudinaryServiceError:
        return _error("Asset deletion could not be confirmed.", 503)
    except SQLAlchemyError:
        db.session.rollback()
        return _error("Asset deletion metadata could not be saved.", 500)
    except Exception:
        # This includes cleanup of a linked external voice clone. The
        # Cloudinary delete is idempotent, so a retry can safely finish the
        # remaining database work without exposing upstream details.
        db.session.rollback()
        return _error("Asset deletion could not be completed.", 503)
