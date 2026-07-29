from flask import Blueprint
from flask_jwt_extended import jwt_required

from app.controllers import ai_controller as ctrl


ai_bp = Blueprint("ai", __name__, url_prefix="/api/ai")


@ai_bp.route("/models", methods=["GET"])
@jwt_required()
def list_models():
    return ctrl.list_available_models()
