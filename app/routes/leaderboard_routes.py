from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import leaderboard_controller as ctrl

leaderboard_bp = Blueprint("leaderboard", __name__, url_prefix="/api/leaderboard")


@leaderboard_bp.route("", methods=["GET"])
@jwt_required()
def get_leaderboard():
    return ctrl.get_leaderboard()
