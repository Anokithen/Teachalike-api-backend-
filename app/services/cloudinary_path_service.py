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
