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
