from functools import wraps

from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, current_user

from app.models.parent_model import ROLE_ADMIN, ROLE_TEACHER, ROLE_PARENT


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


def parent_or_teacher_required(fn):
    return role_required(ROLE_PARENT, ROLE_TEACHER)(fn)


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
