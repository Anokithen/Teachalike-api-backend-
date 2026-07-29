from flask import Blueprint
from app.controllers import voice_profile_controller as ctrl
from app.middleware import parent_or_teacher_required, role_required

voice_profile_bp = Blueprint("voice_profile", __name__, url_prefix="/api/voice-profiles")


@voice_profile_bp.route("", methods=["POST"])
@parent_or_teacher_required
def create_voice_profile():
    return ctrl.create_voice_profile()


@voice_profile_bp.route("", methods=["GET"])
@role_required("parent", "teacher", "admin")
def list_voice_profiles():
    return ctrl.list_voice_profiles()


@voice_profile_bp.route("/<int:voice_profile_id>/status", methods=["GET"])
@role_required("parent", "teacher", "admin")
def get_voice_profile_status(voice_profile_id):
    return ctrl.get_voice_profile_status(voice_profile_id)


@voice_profile_bp.route("/<int:voice_profile_id>/audio", methods=["GET"])
@role_required("parent", "teacher", "admin")
def get_voice_profile_audio(voice_profile_id):
    return ctrl.get_voice_profile_audio(voice_profile_id)


@voice_profile_bp.route("/<int:voice_profile_id>", methods=["PATCH"])
@role_required("parent", "teacher", "admin")
def update_voice_profile(voice_profile_id):
    return ctrl.update_voice_profile(voice_profile_id)


@voice_profile_bp.route("/<int:voice_profile_id>", methods=["DELETE"])
@role_required("parent", "teacher", "admin")
def delete_voice_profile(voice_profile_id):
    return ctrl.delete_voice_profile(voice_profile_id)
