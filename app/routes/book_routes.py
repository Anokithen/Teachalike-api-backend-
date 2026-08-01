from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import book_controller as ctrl
from app.controllers import teacher_book_controller as teacher_ctrl
from app.middleware import approved_teacher_required
from app.controllers import mini_game_controller as game_ctrl

book_bp = Blueprint("book", __name__, url_prefix="/api/books")


@book_bp.route("", methods=["GET"])
@jwt_required()
def list_books():
    return ctrl.list_books()


@book_bp.route("", methods=["POST"])
@approved_teacher_required
def create_book():
    return teacher_ctrl.create_book()


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


@book_bp.route("/<int:book_id>/views", methods=["POST"])
@jwt_required()
def record_view(book_id):
    return ctrl.record_view(book_id)


@book_bp.route("/<int:book_id>/likes/<int:child_id>", methods=["PUT"])
@jwt_required()
def like_book(book_id, child_id):
    return ctrl.like_book(book_id, child_id)


@book_bp.route("/<int:book_id>/likes/<int:child_id>", methods=["DELETE"])
@jwt_required()
def unlike_book(book_id, child_id):
    return ctrl.unlike_book(book_id, child_id)


@book_bp.route("/<int:book_id>/engagement", methods=["GET"])
@jwt_required()
def get_engagement(book_id):
    return ctrl.get_engagement(book_id)
