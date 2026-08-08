from flask import Blueprint
from flask_jwt_extended import jwt_required
from app.controllers import active_child_controller as ctrl
from app.middleware import role_required, active_child_required
bp = Blueprint("active_child", __name__, url_prefix="/api/parent/active-child")
@bp.post("")
@role_required("parent")
def activate(): return ctrl.activate_child()
@bp.get("")
@role_required("parent")
@active_child_required
def current(): return ctrl.get_active_child()
@bp.delete("")
@role_required("parent")
@active_child_required
def lock(): return ctrl.lock_child()
