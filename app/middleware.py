from functools import wraps

from flask import g, jsonify, request
from hashlib import sha256
from app.extensions import db
from app.models.child_access_session_model import ChildAccessSession
from app.models.child_model import Child
from app.utils import utc_now
from flask_jwt_extended import verify_jwt_in_request, current_user

from app.models.parent_model import ROLE_ADMIN, ROLE_TEACHER, ROLE_PARENT
from app.models.teacher_application_model import APPROVAL_APPROVED


def parent_required(fn):
    """Decorator that verifies a valid JWT and that the token maps to a real account."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        if not current_user:
            return jsonify({"error": "Account not found."}), 404
        return fn(*args, **kwargs)

    return wrapper


def role_required(*roles):
    """Decorator factory that verifies a valid JWT and that the account's role
    is one of `roles`. Use e.g. @role_required("admin") or
    @role_required("parent", "teacher").
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            if not current_user:
                return jsonify({"error": "Account not found."}), 404
            if current_user.is_banned:
                return jsonify({"error": "This account has been banned."}), 403
            if current_user.role not in roles:
                return jsonify({"error": "You do not have permission to perform this action."}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def admin_required(fn):
    return role_required(ROLE_ADMIN)(fn)


def teacher_required(fn):
    return role_required(ROLE_TEACHER)(fn)


def approved_teacher_required(fn):
    """Require the teacher role and a currently approved profile."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        if not current_user:
            return jsonify({"error": "Account not found."}), 404
        if current_user.is_banned:
            return jsonify({"error": "This account has been banned."}), 403
        if current_user.role != ROLE_TEACHER:
            return jsonify({"error": "You do not have permission to perform this action."}), 403
        profile = current_user.teacher_application
        if profile is None or profile.approval_status != APPROVAL_APPROVED:
            return jsonify({
                "error": "Only approved teachers can manage books.",
                "error_code": "TEACHER_APPROVAL_REQUIRED",
            }), 403
        return fn(*args, **kwargs)

    return wrapper


def parent_or_teacher_required(fn):
    return role_required(ROLE_PARENT, ROLE_TEACHER)(fn)

def active_child_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        if not current_user: return jsonify({"error":"Account not found."}), 404
        if current_user.role != ROLE_PARENT: return fn(*args, **kwargs)
        raw = request.headers.get("X-Child-Session", "")
        if not raw or len(raw) > 256: return jsonify({"error":"Choose a child from the header before starting this activity.","error_code":"ACTIVE_CHILD_REQUIRED"}), 403
        session = ChildAccessSession.query.filter_by(token_hash=sha256(raw.encode()).hexdigest()).first(); now = utc_now()
        if not session or session.parent_id != current_user.id: return jsonify({"error":"Child session is not valid.","error_code":"ACTIVE_CHILD_SESSION_REVOKED"}), 403
        if session.revoked_at: return jsonify({"error":"Child mode is locked.","error_code":"ACTIVE_CHILD_SESSION_REVOKED"}), 403
        if session.expires_at <= now:
            session.revoked_at = now; session.revoke_reason = "expired"; db.session.commit(); return jsonify({"error":"Child mode has expired.","error_code":"ACTIVE_CHILD_SESSION_EXPIRED"}), 403
        child = db.session.get(Child, session.child_id)
        if not child or child.parent_id != current_user.id: return jsonify({"error":"Active child is no longer available.","error_code":"ACTIVE_CHILD_MISMATCH"}), 403
        if (child.child_access_version or 1) != session.child_access_version: return jsonify({"error":"Child session is no longer valid.","error_code":"ACTIVE_CHILD_SESSION_REVOKED"}), 403
        if (now - session.last_used_at).total_seconds() > 300: session.last_used_at = now; db.session.commit()
        g.active_child, g.child_access_session = child, session
        return fn(*args, **kwargs)
    return wrapper


def get_current_parent():
    """Returns the account tied to the current JWT, or None."""
    return current_user


def child_belongs_to_current_parent(child):
    """Return whether the current account may use a child's activity APIs.

    Despite the historical name, teachers are also allowed to work with the
    children they created on behalf of a parent, and admins may inspect all
    children. Keep this in one helper so nested activity endpoints follow the
    same access policy as the child profile endpoints.
    """
    return can_access_child(child)


def can_access_child(child):
    """True if the current account may view/manage this child: either the
    owning parent, or an admin (who can see everyone's data)."""
    if child is None or current_user is None:
        return False
    if current_user.is_admin:
        return True
    return child.parent_id == current_user.id or (
        current_user.is_teacher and child.created_by_id == current_user.id
    )


def voice_profile_belongs_to_current_parent(voice_profile):
    return (
        voice_profile is not None
        and current_user is not None
        and voice_profile.parent_id == current_user.id
    )


def can_access_voice_profile(voice_profile):
    return (
        voice_profile is not None
        and current_user is not None
        and (current_user.is_admin or voice_profile.parent_id == current_user.id)
    )


def owns_voice_profile(voice_profile):
    """Only the parent/teacher who uploaded a voice profile may delete it."""
    return (
        voice_profile is not None
        and current_user is not None
        and current_user.role in (ROLE_PARENT, ROLE_TEACHER)
        and voice_profile.parent_id == current_user.id
    )


def can_access_book_narration(narration):
    """Narrations inherit access from the private voice profile they use."""
    return (
        narration is not None
        and narration.voice_profile is not None
        and current_user is not None
        and (current_user.is_admin or narration.voice_profile.parent_id == current_user.id)
    )
