from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import parent_controller as ctrl

parent_bp = Blueprint("parent", __name__, url_prefix="/api/parents")


@parent_bp.route("/me", methods=["GET"])
@jwt_required()
def get_me():
    return ctrl.get_me()


@parent_bp.route("/me", methods=["PATCH"])
@jwt_required()
def update_me():
    return ctrl.update_me()


@parent_bp.route("/me", methods=["DELETE"])
@jwt_required()
def delete_me():
    return ctrl.delete_me()


@parent_bp.route("/me/profile-image", methods=["POST"])
@jwt_required()
def upload_profile_image():
    return ctrl.upload_profile_image_for_current_user()


@parent_bp.route("/me/profile-image", methods=["DELETE"])
@jwt_required()
def delete_profile_image():
    return ctrl.delete_profile_image_for_current_user()
