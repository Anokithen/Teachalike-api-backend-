from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import sync_controller as ctrl

sync_bp = Blueprint("sync", __name__, url_prefix="/api/sync")


@sync_bp.route("", methods=["POST"])
@jwt_required()
def sync_offline_activity():
    return ctrl.sync_offline_activity()
