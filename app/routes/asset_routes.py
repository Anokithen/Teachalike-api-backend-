"""Routes for Cloudinary-backed asset metadata."""

from flask import Blueprint

from app.controllers import asset_controller as ctrl
from app.middleware import admin_required, parent_or_teacher_required, role_required

asset_bp = Blueprint("asset", __name__)


@asset_bp.post("/api/assets/profile-image")
@role_required("parent", "teacher", "admin")
def upload_profile_image():
    return ctrl.upload_user_profile_image()


@asset_bp.post("/api/assets/children/<int:child_id>/profile-image")
@role_required("parent", "teacher", "admin")
def upload_child_profile_image(child_id):
    return ctrl.upload_child_profile_image(child_id)


@asset_bp.post("/api/assets/voice-profiles")
@parent_or_teacher_required
def upload_voice_profile():
    return ctrl.upload_voice_profile()


@asset_bp.post("/api/assets/books/<int:book_id>/narrations")
@role_required("parent", "teacher", "admin")
def upload_book_narration(book_id):
    return ctrl.upload_book_narration(book_id)
