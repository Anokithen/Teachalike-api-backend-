from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import reading_session_controller as ctrl
from app.controllers import feedback_controller as feedback_ctrl

reading_session_bp = Blueprint("reading_session", __name__, url_prefix="/api/reading-sessions")


@reading_session_bp.route("", methods=["POST"])
@jwt_required()
def create_reading_session():
    return ctrl.create_reading_session()


@reading_session_bp.route("/<int:session_id>", methods=["PATCH"])
@jwt_required()
def update_reading_session(session_id):
    return ctrl.update_reading_session(session_id)


@reading_session_bp.route("/<int:session_id>", methods=["GET"])
@jwt_required()
def get_reading_session(session_id):
    return ctrl.get_reading_session(session_id)


@reading_session_bp.route("/<int:session_id>/pronunciation-check", methods=["POST"])
@jwt_required()
def check_pronunciation(session_id):
    return ctrl.check_pronunciation(session_id)


@reading_session_bp.route("/<int:session_id>/pronunciation-transcript", methods=["POST"])
@jwt_required()
def transcribe_pronunciation(session_id):
    return ctrl.transcribe_pronunciation(session_id)


@reading_session_bp.route("/<int:session_id>/feedback", methods=["POST"])
@jwt_required()
def create_feedback(session_id):
    return feedback_ctrl.create_feedback(session_id)


@reading_session_bp.route("/<int:session_id>/feedback", methods=["GET"])
@jwt_required()
def list_feedback(session_id):
    return feedback_ctrl.list_feedback(session_id)
