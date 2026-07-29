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


@asset_bp.post("/api/admin/books/<int:book_id>/videos")
@admin_required
def upload_book_video(book_id):
    return ctrl.upload_book_video(book_id)


@asset_bp.delete("/api/assets/<int:asset_id>")
@role_required("parent", "teacher", "admin")
def delete_asset(asset_id):
    return ctrl.delete_stored_asset(asset_id)


@asset_bp.get("/api/assets/<int:asset_id>")
@role_required("parent", "teacher", "admin")
def get_asset(asset_id):
    return ctrl.get_asset(asset_id)


@asset_bp.get("/api/books/<int:book_id>/assets")
@role_required("parent", "teacher", "admin")
def list_book_assets(book_id):
    return ctrl.list_book_assets(book_id)


@asset_bp.get("/api/users/me/assets")
@role_required("parent", "teacher", "admin")
def list_my_assets():
    return ctrl.list_my_assets()
