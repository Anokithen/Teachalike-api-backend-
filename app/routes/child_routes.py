from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import child_controller as ctrl
from app.controllers import reading_session_controller as session_ctrl
from app.controllers import game_result_controller as result_ctrl
from app.controllers import leaderboard_controller as leaderboard_ctrl
from app.middleware import parent_or_teacher_required

child_bp = Blueprint("child", __name__, url_prefix="/api/children")


@child_bp.route("", methods=["POST"])
@parent_or_teacher_required
def create_child():
    return ctrl.create_child()


@child_bp.route("", methods=["GET"])
@jwt_required()
def list_children():
    return ctrl.list_children()


@child_bp.route("/<int:child_id>", methods=["GET"])
@jwt_required()
def get_child(child_id):
    return ctrl.get_child(child_id)


@child_bp.route("/<int:child_id>", methods=["PATCH"])
@jwt_required()
def update_child(child_id):
    return ctrl.update_child(child_id)


@child_bp.route("/<int:child_id>/verify-pin", methods=["POST"])
@jwt_required()
def verify_child_pin(child_id):
    return ctrl.verify_child_pin(child_id)


@child_bp.route("/<int:child_id>", methods=["DELETE"])
@jwt_required()
def delete_child(child_id):
    return ctrl.delete_child(child_id)


@child_bp.route("/<int:child_id>/profile-image", methods=["POST"])
@jwt_required()
def upload_profile_image(child_id):
    return ctrl.upload_profile_image_for_child(child_id)


@child_bp.route("/<int:child_id>/profile-image", methods=["DELETE"])
@jwt_required()
def delete_profile_image(child_id):
    return ctrl.delete_profile_image_for_child(child_id)


# --- nested resources that only make sense in the context of a child ---

@child_bp.route("/<int:child_id>/reading-sessions", methods=["GET"])
@jwt_required()
def list_child_reading_sessions(child_id):
    return session_ctrl.list_child_reading_sessions(child_id)


@child_bp.route("/<int:child_id>/game-results", methods=["GET"])
@jwt_required()
def list_child_game_results(child_id):
    return result_ctrl.list_child_game_results(child_id)


@child_bp.route("/<int:child_id>/leaderboard-entry", methods=["GET"])
@jwt_required()
def get_child_leaderboard_entry(child_id):
    return leaderboard_ctrl.get_child_leaderboard_entry(child_id)
