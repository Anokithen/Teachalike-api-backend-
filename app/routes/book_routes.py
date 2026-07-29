from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import book_controller as ctrl
from app.controllers import mini_game_controller as game_ctrl

book_bp = Blueprint("book", __name__, url_prefix="/api/books")


@book_bp.route("", methods=["GET"])
@jwt_required()
def list_books():
    return ctrl.list_books()


@book_bp.route("/<int:book_id>", methods=["GET"])
@jwt_required()
def get_book(book_id):
    return ctrl.get_book(book_id)


@book_bp.route("/<int:book_id>/download", methods=["GET"])
@jwt_required()
def download_book(book_id):
    return ctrl.download_book(book_id)


@book_bp.route("/<int:book_id>/mini-games", methods=["GET"])
@jwt_required()
def list_book_mini_games(book_id):
    return game_ctrl.list_book_mini_games(book_id)
