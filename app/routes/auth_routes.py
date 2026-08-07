from flask import Blueprint
from app.controllers import auth_controller as ctrl

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.route("/register", methods=["POST"])
def register():
    return ctrl.register()


@auth_bp.route("/login", methods=["POST"])
def login():
    return ctrl.login()


@auth_bp.route("/google", methods=["POST"])
def google_auth():
    return ctrl.google_auth()


@auth_bp.route("/verify-email", methods=["POST"])
def verify_email():
    return ctrl.verify_email()


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    return ctrl.resend_verification()


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    return ctrl.refresh()


@auth_bp.route("/logout", methods=["POST"])
def logout():
    return ctrl.logout()
