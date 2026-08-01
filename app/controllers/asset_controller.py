"""Authenticated asset upload, query, and deletion workflows."""

import re

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.middleware import can_access_child
from app.models.asset_model import (
    Asset,
    BOOK_COVER_IMAGE,
    BOOK_IMAGE,
    BOOK_ILLUSTRATION,
    BOOK_VIDEO,
    CHILD_PROFILE_IMAGE,
    GENERATED_BOOK_AUDIO,
    STATUS_COMPLETED,
    STATUS_CLEANUP_FAILED,
    STATUS_DELETED,
    USER_PROFILE_IMAGE,
    VOICE_PROFILE,
    TEACHER_BOOK_AUDIO,
)
from app.models.book_model import Book
from app.models.book_narration_model import BookNarration, STATUS_READY
from app.models.child_model import Child
from app.models.parent_model import Parent
from app.models.voice_profile_model import VoiceProfile
from app.services.cloudinary_path_service import (
    get_book_images_folder_from_root,
    get_book_video_folder_from_root,
    get_child_profile_folder,
    get_generated_book_audio_folder,
    get_user_profile_folder,
    get_teacher_book_audio_folder_from_root,
)
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_asset,
    upload_asset,
    validate_upload_size,
    validate_uploaded_file,
    stream_authenticated_audio,
)
from app.utils import utc_now
from app.services.elevenlabs_service import delete_voice
from app.services.book_management_service import ensure_book_asset_root
from app.models.teacher_application_model import APPROVAL_APPROVED

SIZE_CONFIG = {
    USER_PROFILE_IMAGE: "MAX_PROFILE_IMAGE_SIZE_MB",
    CHILD_PROFILE_IMAGE: "MAX_CHILD_IMAGE_SIZE_MB",
    VOICE_PROFILE: "MAX_VOICE_PROFILE_SIZE_MB",
    GENERATED_BOOK_AUDIO: "MAX_BOOK_AUDIO_SIZE_MB",
    BOOK_VIDEO: "MAX_BOOK_VIDEO_SIZE_MB",
    BOOK_IMAGE: "MAX_PROFILE_IMAGE_SIZE_MB",
    TEACHER_BOOK_AUDIO: "MAX_BOOK_AUDIO_SIZE_MB",
}
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


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


def _validated_optional_language():
    """Validate an optional BCP-47-style narration language tag."""
    language = str(request.form.get("language") or "").strip()
    if not language:
        return None, None
    if len(language) > 35 or not LANGUAGE_PATTERN.fullmatch(language):
        return None, _error("language must be a valid language tag.", 422)
    return language, None


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
        if existing and existing.cloudinary_public_id == metadata["public_id"]:
            # A deterministic Cloudinary overwrite has already replaced the
            # old bytes. Deleting it here would leave both the old DB row and
            # related profile with no deliverable asset. Keep the confirmed
            # replacement for a safe metadata retry.
            current_app.logger.error(
                "Profile metadata save failed after Cloudinary replacement asset_id=%s",
                metadata.get("asset_id"),
            )
        else:
            # A renamed child's canonical folder creates a different public
            # ID. The previous image remains intact, so this new orphan can be
            # removed safely when its metadata transaction fails.
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
                "parent": current_user.to_self_dict(),
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
            "parent": current_user.to_self_dict(),
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
            "parent": current_user.to_self_dict(),
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
    language, error = _validated_optional_language()
    if error:
        return error
    narration = BookNarration(
        book_id=book.id,
        voice_profile_id=profile.id,
        status=STATUS_READY,
        language=language,
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


def _can_manage_catalog_book(book):
    if current_user.is_admin:
        return True
    return (
        current_user.is_teacher
        and not current_user.is_banned
        and current_user.teacher_application is not None
        and current_user.teacher_application.approval_status == APPROVAL_APPROVED
        and book.created_by_account_id == current_user.id
    )


def _book_asset_owner_id(book):
    return book.created_by_account_id or current_user.id


def _retire_asset(asset):
    asset.status = STATUS_DELETED
    asset.deleted_at = utc_now()
    asset.active_slot = None


def _cleanup_replaced_asset(existing, metadata):
    if not existing or existing.cloudinary_public_id == metadata["public_id"]:
        return
    try:
        delete_asset(
            existing.cloudinary_public_id,
            existing.cloudinary_resource_type,
            existing.cloudinary_delivery_type,
        )
    except CloudinaryServiceError:
        existing.status = STATUS_CLEANUP_FAILED
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()


def upload_book_image(book_id):
    book = db.session.get(Book, book_id)
    if book is None:
        return _error("Book not found.", 404)
    if not _can_manage_catalog_book(book):
        return _error("You cannot manage this book.", 403)
    upload, error = _validated_file(BOOK_IMAGE, "image")
    if error:
        return error
    kind = str(request.form.get("image_kind") or "").strip().lower()
    if kind not in {"cover", "picture"}:
        return _error("image_kind must be cover or picture.", 400)
    if kind == "picture":
        try:
            position = int(request.form.get("position"))
        except (TypeError, ValueError):
            return _error("position must be an integer from 1 to 8.", 400)
        if position < 1 or position > 8:
            return _error("position must be an integer from 1 to 8.", 400)
        if position > len(book.image_urls or []) + 1:
            return _error("Story pictures must be uploaded in order.", 409)
        public_name = f"picture_{position:02d}"
        slot = f"book:{book.id}:picture:{position:02d}"
    else:
        position = None
        public_name = "cover"
        slot = f"book:{book.id}:cover"
    existing = Asset.query.filter_by(active_slot=slot, deleted_at=None).first()
    root = ensure_book_asset_root(book)
    folder = get_book_images_folder_from_root(root)
    metadata = None
    try:
        metadata = upload_asset(
            upload, folder, resource_type="image",
            public_id=f"{folder}/{public_name}", overwrite=existing is not None,
            tags=[BOOK_IMAGE.lower(), f"book_{book.id}"],
            context={"book_id": str(book.id)},
        )
        if existing:
            _retire_asset(existing)
        asset = _new_asset(
            metadata, BOOK_IMAGE, _book_asset_owner_id(book), book_id=book.id,
            admin_id=current_user.id if current_user.is_admin else None,
            active_slot=slot,
        )
        db.session.add(asset)
        if kind == "cover":
            book.cover_image_url = metadata["secure_url"]
        else:
            urls = list(book.image_urls or [])
            if position == len(urls) + 1:
                urls.append(metadata["secure_url"])
            else:
                urls[position - 1] = metadata["secure_url"]
            book.image_urls = urls
        db.session.commit()
        _cleanup_replaced_asset(existing, metadata)
        return _response("Book image uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        db.session.rollback()
        return _error("Book image upload failed.", 503)
    except SQLAlchemyError:
        db.session.rollback()
        if metadata and not (
            existing and existing.cloudinary_public_id == metadata["public_id"]
        ):
            _cleanup_upload(metadata)
        return _error("Book image metadata could not be saved.", 500)


def upload_book_video(book_id):
    """Store or replace a catalog book's first video."""
    book = db.session.get(Book, book_id)
    if book is None:
        return _error("Book not found.", 404)
    if not _can_manage_catalog_book(book):
        return _error("You cannot manage this book.", 403)
    upload, error = _validated_file(BOOK_VIDEO, "video")
    if error:
        return error
    metadata = None
    slot = f"book:{book.id}:video:01"
    existing = Asset.query.filter_by(active_slot=slot, deleted_at=None).first()
    try:
        root = ensure_book_asset_root(book)
        folder = get_book_video_folder_from_root(root)
        metadata = upload_asset(
            upload,
            folder,
            resource_type="video",
            public_id=f"{folder}/video_01",
            overwrite=existing is not None,
        )
        if existing:
            _retire_asset(existing)
        asset = _new_asset(
            metadata,
            BOOK_VIDEO,
            _book_asset_owner_id(book),
            admin_id=current_user.id if current_user.is_admin else None,
            book_id=book.id,
            active_slot=slot,
        )
        db.session.add(asset)
        book.video_url = metadata["secure_url"]
        db.session.commit()
        _cleanup_replaced_asset(existing, metadata)
        return _response("Book video uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        db.session.rollback()
        return _error("Book video upload failed.", 503)
    except SQLAlchemyError:
        db.session.rollback()
        if metadata and not (
            existing and existing.cloudinary_public_id == metadata["public_id"]
        ):
            _cleanup_upload(metadata)
        return _error("Book video metadata could not be saved.", 500)


def upload_teacher_book_audio(book_id):
    book = db.session.get(Book, book_id)
    if book is None:
        return _error("Book not found.", 404)
    if not _can_manage_catalog_book(book):
        return _error("You cannot manage this book.", 403)
    upload, error = _validated_file(TEACHER_BOOK_AUDIO, "audio")
    if error:
        return error
    slot = f"book:{book.id}:teacher_audio"
    existing = Asset.query.filter_by(active_slot=slot, deleted_at=None).first()
    metadata = None
    try:
        root = ensure_book_asset_root(book)
        folder = get_teacher_book_audio_folder_from_root(root)
        metadata = upload_asset(
            upload, folder, resource_type="video",
            public_id=f"{folder}/voice_audio_teacher",
            overwrite=existing is not None, delivery_type="authenticated",
            tags=[TEACHER_BOOK_AUDIO.lower(), f"book_{book.id}"],
            context={"book_id": str(book.id)},
        )
        if existing:
            _retire_asset(existing)
        asset = _new_asset(
            metadata, TEACHER_BOOK_AUDIO, _book_asset_owner_id(book),
            book_id=book.id,
            admin_id=current_user.id if current_user.is_admin else None,
            active_slot=slot,
        )
        db.session.add(asset)
        db.session.commit()
        _cleanup_replaced_asset(existing, metadata)
        return _response("Teacher narration uploaded.", asset.to_dict(), 201)
    except CloudinaryServiceError:
        db.session.rollback()
        return _error("Teacher narration upload failed.", 503)
    except SQLAlchemyError:
        db.session.rollback()
        if metadata and not (
            existing and existing.cloudinary_public_id == metadata["public_id"]
        ):
            _cleanup_upload(metadata)
        return _error("Teacher narration metadata could not be saved.", 500)


def get_teacher_book_audio(book_id):
    if db.session.get(Book, book_id) is None:
        return _error("Book not found.", 404)
    asset = Asset.query.filter_by(
        book_id=book_id, asset_category=TEACHER_BOOK_AUDIO,
        active_slot=f"book:{book_id}:teacher_audio", deleted_at=None,
    ).first()
    if asset is None:
        return _error("This book has no teacher narration.", 404)
    try:
        return stream_authenticated_audio(
            asset.cloudinary_public_id,
            asset.cloudinary_secure_url,
            current_app.config,
            request.headers.get("Range"),
        )
    except CloudinaryServiceError:
        return _error("Teacher narration playback is temporarily unavailable.", 503)


def delete_teacher_book_audio(book_id):
    book = db.session.get(Book, book_id)
    if book is None:
        return _error("Book not found.", 404)
    if not _can_manage_catalog_book(book):
        return _error("You cannot manage this book.", 403)
    asset = Asset.query.filter_by(
        book_id=book.id, asset_category=TEACHER_BOOK_AUDIO,
        active_slot=f"book:{book.id}:teacher_audio", deleted_at=None,
    ).first()
    if asset is None:
        return _response("Teacher narration was already removed.")
    return delete_stored_asset(asset.id)


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
    asset_slot = asset.active_slot
    if (
        asset.asset_category == BOOK_IMAGE
        and asset.book_id
        and asset_slot
        and ":picture:" in asset_slot
    ):
        try:
            position = int(asset_slot.rsplit(":", 1)[-1])
        except ValueError:
            position = 0
        higher_picture = Asset.query.filter(
            Asset.book_id == asset.book_id,
            Asset.asset_category == BOOK_IMAGE,
            Asset.deleted_at.is_(None),
            Asset.active_slot.isnot(None),
            Asset.active_slot > f"book:{asset.book_id}:picture:{position:02d}",
        ).first()
        if higher_picture:
            return _error("Delete later story pictures first to preserve their order.", 409)
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
        elif asset.asset_category == BOOK_COVER_IMAGE and asset.book_id:
            book = db.session.get(Book, asset.book_id)
            if book and book.cover_image_url == asset.cloudinary_secure_url:
                book.cover_image_url = None
        elif asset.asset_category == BOOK_ILLUSTRATION and asset.book_id:
            book = db.session.get(Book, asset.book_id)
            if book:
                book.image_urls = [
                    url for url in (book.image_urls or [])
                    if url != asset.cloudinary_secure_url
                ]
        elif asset.asset_category == BOOK_IMAGE and asset.book_id:
            book = db.session.get(Book, asset.book_id)
            if book and asset_slot == f"book:{book.id}:cover":
                if book.cover_image_url == asset.cloudinary_secure_url:
                    book.cover_image_url = None
            elif book and asset_slot and ":picture:" in asset_slot:
                book.image_urls = [
                    url for url in (book.image_urls or [])
                    if url != asset.cloudinary_secure_url
                ]
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
