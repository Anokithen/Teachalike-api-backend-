from flask import Blueprint

from app.controllers import book_narration_controller as ctrl
from app.middleware import role_required


book_narration_bp = Blueprint("book_narration", __name__)


@book_narration_bp.route("/api/books/<int:book_id>/narrations", methods=["POST"])
@role_required("parent", "teacher", "admin")
def create_book_narration(book_id):
    return ctrl.create_book_narration(book_id)


@book_narration_bp.route("/api/books/<int:book_id>/narrations", methods=["GET"])
@role_required("parent", "teacher", "admin")
def list_book_narrations(book_id):
    return ctrl.list_book_narrations(book_id)


@book_narration_bp.route("/api/book-narrations/<int:narration_id>/status", methods=["GET"])
@role_required("parent", "teacher", "admin")
def get_book_narration_status(narration_id):
    return ctrl.get_book_narration_status(narration_id)


@book_narration_bp.route("/api/book-narrations/<int:narration_id>/audio", methods=["GET"])
@role_required("parent", "teacher", "admin")
def get_book_narration_audio(narration_id):
    return ctrl.get_book_narration_audio(narration_id)
