from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.asset_model import Asset, VOICE_PROFILE
from app.models.voice_profile_model import VoiceProfile, STATUS_READY
from app.middleware import can_access_voice_profile, owns_voice_profile
from app.services.cloudinary_path_service import get_voice_profile_folder
from app.services.cloudinary_service import (
    CloudinaryServiceError,
    delete_voice_sample,
    stream_authenticated_audio,
    upload_asset,
    validate_upload_size,
    validate_uploaded_file,
)
from app.services.elevenlabs_service import ElevenLabsError, clone_voice, delete_voice


def _asset_response(message, data=None, status=200):
    return jsonify({"success": True, "message": message, "data": data}), status


def _asset_error(message, status):
    return jsonify({"success": False, "message": message, "data": None}), status


def _cleanup_failed_voice_upload(metadata, elevenlabs_voice_id=None):
    if elevenlabs_voice_id:
        try:
            delete_voice(elevenlabs_voice_id, current_app.config)
        except Exception:
            current_app.logger.exception(
                "Could not clean up a failed ElevenLabs voice clone"
            )
    if metadata:
        try:
            delete_voice_sample(metadata["public_id"], current_app.config)
        except Exception:
            current_app.logger.exception(
                "Could not clean up a failed voice-profile upload"
            )


def create_voice_profile(asset_response=False):
    """Clone and persist one private voice through canonical asset storage."""
    sample = request.files.get("file") or request.files.get("audio")
    if not sample or not sample.filename:
        if asset_response:
            return _asset_error("A supported audio file is required.", 400)
        return jsonify({"errors": ["A supported audio file is required."]}), 400
    try:
        extension = validate_uploaded_file(sample, "audio")
    except ValueError as exc:
        if asset_response:
            return _asset_error(str(exc), 415)
        return jsonify({"errors": [str(exc)]}), 415
    limit_mb = current_app.config["MAX_VOICE_PROFILE_SIZE_MB"]
    try:
        validate_upload_size(sample, limit_mb)
    except ValueError:
        message = f"The file exceeds the {limit_mb} MB limit."
        if asset_response:
            return _asset_error(message, 413)
        return jsonify({"error": message}), 413

    data = request.form
    label = str(data.get("label") or "").strip()
    if len(label) > 80:
        if asset_response:
            return _asset_error("label must be 80 characters or fewer.", 422)
        return jsonify({"errors": ["label must be 80 characters or fewer."]}), 400

    metadata = None
    elevenlabs_voice_id = None
    voice_profile = VoiceProfile(
        parent_id=current_user.id,
        label=label or None,
        voice_sample_url="pending",
        status=STATUS_READY,
    )
    try:
        db.session.add(voice_profile)
        db.session.flush()
        folder = get_voice_profile_folder(current_user.id)
        metadata = upload_asset(
            sample,
            folder,
            resource_type="video",
            public_id=f"{folder}/voice_profile_{voice_profile.id}",
            overwrite=False,
            delivery_type="authenticated",
            format=extension,
            tags=[VOICE_PROFILE.lower()],
        )
        # Cloudinary consumes the upload stream. Rewind it before sending the
        # same sample to ElevenLabs for Instant Voice Cloning.
        sample.stream.seek(0)
        elevenlabs_voice_id = clone_voice(
            sample.stream,
            sample.filename,
            sample.mimetype,
            current_app.config,
            profile_label=label,
            owner_name=current_user.name,
        )
        voice_profile.voice_sample_url = metadata["secure_url"]
        voice_profile.cloudinary_public_id = metadata["public_id"]
        voice_profile.elevenlabs_voice_id = elevenlabs_voice_id
        asset = Asset.from_cloudinary_metadata(
            metadata,
            category=VOICE_PROFILE,
            owner_user_id=current_user.id,
            voice_profile_id=voice_profile.id,
        )
        db.session.add(asset)
        db.session.commit()

        if asset_response:
            return _asset_response(
                "Voice profile uploaded and cloned.",
                asset.to_dict(),
                201,
            )
        return jsonify(
            {
                "message": "Voice profile cloned securely and ready for book narration.",
                "voice_profile": voice_profile.to_dict(),
            }
        ), 201
    except (CloudinaryServiceError, ElevenLabsError) as exc:
        db.session.rollback()
        _cleanup_failed_voice_upload(metadata, elevenlabs_voice_id)
        message = str(exc)
        if asset_response:
            return _asset_error(message, 503)
        return jsonify({"error": message}), 503
    except SQLAlchemyError:
        db.session.rollback()
        _cleanup_failed_voice_upload(metadata, elevenlabs_voice_id)
        if asset_response:
            return _asset_error("Voice profile metadata could not be saved.", 500)
        return jsonify({"error": "Voice profile metadata could not be saved."}), 500
    except Exception:
        db.session.rollback()
        _cleanup_failed_voice_upload(metadata, elevenlabs_voice_id)
        if asset_response:
            return _asset_error("An internal server error occurred.", 500)
        return jsonify({"error": "An internal server error occurred."}), 500


def list_voice_profiles():
    query = VoiceProfile.query
    if not current_user.is_admin:
        query = query.filter_by(parent_id=current_user.id)
    profiles = query.order_by(VoiceProfile.id.desc()).all()
    return jsonify({"voice_profiles": [p.to_dict() for p in profiles]}), 200
