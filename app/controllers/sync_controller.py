from datetime import datetime, timezone
import math

from flask import jsonify, request
from flask_jwt_extended import current_user

from app.extensions import db
from app.utils import utc_now
from app.models.child_model import Child
from app.models.book_model import Book
from app.models.reading_session_model import ReadingSession
from app.models.voice_profile_model import VoiceProfile, STATUS_READY as VOICE_STATUS_READY
from app.models.feedback_model import Feedback, FEEDBACK_TYPES
from app.models.game_result_model import GameResult
from app.models.mini_game_model import MiniGame
from app.middleware import child_belongs_to_current_parent
from app.middleware import voice_profile_belongs_to_current_parent
from app.controllers.game_result_controller import _award_leaderboard_points, _maximum_game_score

MAX_SYNC_ITEMS_PER_TYPE = 100
MAX_PROGRESS_ENTRIES = 1000
MAX_FEEDBACK_TEXT_LENGTH = 2000


def _parse_sync_datetime(value, field_name):
    """Parse the ISO timestamps emitted by offline clients."""
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        return value, None
    if not isinstance(value, str):
        return None, f"{field_name} must be an ISO-8601 timestamp."
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, f"{field_name} must be an ISO-8601 timestamp."
    # Store naive UTC datetimes because the existing MySQL schema uses
    # DATETIME rather than TIMESTAMP WITH TIME ZONE.
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed, None


def _as_list(value, field_name):
    if value is None:
        return [], None
    if not isinstance(value, list):
        return [], f"{field_name} must be an array."
    return value, None


def _get_by_id(model, value):
    """Safely resolve an integer primary key from untrusted sync JSON."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        identifier = int(value)
    except (TypeError, ValueError):
        return None
    return db.session.get(model, identifier) if identifier > 0 else None
