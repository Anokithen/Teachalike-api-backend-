from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import mini_game_controller as ctrl
from app.controllers import game_result_controller as result_ctrl

mini_game_bp = Blueprint("mini_game", __name__, url_prefix="/api/mini-games")


@mini_game_bp.route("/<int:game_id>", methods=["GET"])
@jwt_required()
def get_mini_game(game_id):
    return ctrl.get_mini_game(game_id)


@mini_game_bp.route("/<int:game_id>/results", methods=["POST"])
@jwt_required()
def submit_game_result(game_id):
    return result_ctrl.submit_game_result(game_id)
