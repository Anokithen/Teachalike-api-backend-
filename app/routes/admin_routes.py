from flask import Blueprint
from app.controllers import admin_controller as ctrl
from app.middleware import admin_required
from app.models.parent_model import ROLE_PARENT, ROLE_TEACHER

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


# --- Parents -----------------------------------------------------------

@admin_bp.route("/parents", methods=["GET"])
@admin_required
def list_parents():
    return ctrl.list_parents()


@admin_bp.route("/parents", methods=["POST"])
@admin_required
def register_parent():
    return ctrl.register_parent()


@admin_bp.route("/parents/<int:parent_id>", methods=["GET"])
@admin_required
def get_parent(parent_id):
    return ctrl.get_parent(parent_id)


@admin_bp.route("/parents/<int:parent_id>/ban", methods=["PATCH"])
@admin_required
def ban_parent(parent_id):
    return ctrl.ban_account(parent_id, ROLE_PARENT)


@admin_bp.route("/parents/<int:parent_id>/unban", methods=["PATCH"])
@admin_required
def unban_parent(parent_id):
    return ctrl.unban_account(parent_id, ROLE_PARENT)


@admin_bp.route("/parents/<int:parent_id>", methods=["DELETE"])
@admin_required
def delete_parent(parent_id):
    return ctrl.delete_account(parent_id, ROLE_PARENT)


# --- Teachers ------------------------------------------------------------

@admin_bp.route("/teachers", methods=["GET"])
@admin_required
def list_teachers():
    return ctrl.list_teachers()


@admin_bp.route("/teachers", methods=["POST"])
@admin_required
def register_teacher():
    return ctrl.register_teacher()
