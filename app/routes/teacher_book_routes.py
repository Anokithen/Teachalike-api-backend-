from flask import Blueprint

from app.controllers import teacher_book_controller as ctrl
from app.middleware import approved_teacher_required

teacher_book_bp = Blueprint("teacher_book", __name__, url_prefix="/api/teacher/books")


@teacher_book_bp.get("")
@approved_teacher_required
def list_books():
    return ctrl.list_books()


@teacher_book_bp.get("/<int:book_id>")
@approved_teacher_required
def get_book(book_id):
    return ctrl.get_book(book_id)


@teacher_book_bp.patch("/<int:book_id>")
@approved_teacher_required
def update_book(book_id):
    return ctrl.update_book(book_id)


@teacher_book_bp.delete("/<int:book_id>")
@approved_teacher_required
def delete_book(book_id):
    return ctrl.delete_book(book_id)
