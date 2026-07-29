"""Canonical Cloudinary asset-folder construction.

All paths are server-derived.  User supplied folder or path values must never
be passed to these helpers as complete paths.
"""

import re

from flask import current_app, has_app_context

MAX_SEGMENT_LENGTH = 80


def sanitize_folder_segment(value) -> str:
    """Return a bounded, traversal-safe Cloudinary folder segment."""
    segment = str(value or "").lower().replace(" ", "_")
    segment = segment.replace("..", "_").replace("/", "_").replace("\\", "_")
    segment = re.sub(r"[^a-z0-9_-]+", "_", segment)
    segment = re.sub(r"[_-]{2,}", "_", segment).strip("_-")
    return (segment or "unnamed")[:MAX_SEGMENT_LENGTH].rstrip("_-") or "unnamed"


def _root() -> str:
    value = (
        current_app.config.get("CLOUDINARY_ROOT_FOLDER", "teachalike")
        if has_app_context()
        else "teachalike"
    )
    return sanitize_folder_segment(value)


def get_user_root_folder(user_id) -> str:
    """Return the immutable root for one account."""
    return f"{_root()}/{int(user_id)}"


def get_user_profile_folder(user_id) -> str:
    """Return the account profile-image folder."""
    return f"{get_user_root_folder(user_id)}/Image/Profile"


def get_child_profile_folder(user_id, child_id, child_name) -> str:
    """Return a child profile-image folder containing the child database ID."""
    child = f"{int(child_id)}_{sanitize_folder_segment(child_name)}"
    return f"{get_user_root_folder(user_id)}/Image/Children_profile/{child}"


def get_voice_profile_folder(user_id) -> str:
    """Return the account voice-profile folder."""
    return f"{get_user_root_folder(user_id)}/Audio/Voice_profiles"


def get_generated_book_audio_folder(user_id, book_id, book_name) -> str:
    """Return a book narration folder containing the book database ID."""
    book = f"{int(book_id)}_{sanitize_folder_segment(book_name)}"
    return f"{get_user_root_folder(user_id)}/Audio/Generated_Books_Audio/{book}"


def get_book_video_folder(user_id, admin_id, book_id, book_name) -> str:
    """Return an administrator's video folder for a catalog book."""
    book = f"{int(book_id)}_{sanitize_folder_segment(book_name)}"
    return f"{get_user_root_folder(user_id)}/Video/{int(admin_id)}/{book}"
