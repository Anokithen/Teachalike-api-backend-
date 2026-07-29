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


def sync_offline_activity():
    """POST /api/sync

    Batch-uploads reading sessions, feedback, and game results recorded while
    offline, and reconciles them in one transaction.

    Expected payload shape:
    {
      "reading_sessions": [
        {"client_id": "local-1", "child_id": 1, "book_id": 3, "voice_profile_id": 2,
         "started_at": "...", "completed_at": "...", "accuracy_score": 92,
         "progress_log": [...] }
      ],
      "feedback": [
        {"session_client_id": "local-1", "feedback_text": "...", "feedback_type": "praise"}
      ],
      "game_results": [
        {"child_id": 1, "game_id": 5, "score": 40, "completed_at": "..."}
      ]
    }

    `client_id` / `session_client_id` let the mobile app reference a session
    created earlier in the same batch, since it won't have a server-side id yet.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required."}), 400

    reading_sessions_in, reading_error = _as_list(data.get("reading_sessions"), "reading_sessions")
    feedback_in, feedback_error = _as_list(data.get("feedback"), "feedback")
    game_results_in, games_error = _as_list(data.get("game_results"), "game_results")
    list_errors = [error for error in (reading_error, feedback_error, games_error) if error]
    for field_name, entries in (
        ("reading_sessions", reading_sessions_in),
        ("feedback", feedback_in),
        ("game_results", game_results_in),
    ):
        if len(entries) > MAX_SYNC_ITEMS_PER_TYPE:
            list_errors.append(
                f"{field_name} cannot contain more than {MAX_SYNC_ITEMS_PER_TYPE} entries."
            )
    if list_errors:
        return jsonify({"errors": list_errors}), 400

    client_id_to_session = {}
    created_sessions, created_feedback, created_results = [], [], []
    errors = []

    try:
        # 1. Reading sessions first, so feedback can reference them by client_id.
        for item in reading_sessions_in:
            if not isinstance(item, dict):
                errors.append("reading_session entries must be objects.")
                continue
            child_id = item.get("child_id")
            child = _get_by_id(Child, child_id)
            if not child_id or not child_belongs_to_current_parent(child):
                errors.append(f"reading_session with client_id={item.get('client_id')}: invalid or unauthorized child_id.")
                continue
            child_id = child.id

            book_id = item.get("book_id")
            book = _get_by_id(Book, book_id)
            if not book:
                errors.append(f"reading_session with client_id={item.get('client_id')}: invalid book_id.")
                continue
            book_id = book.id

            voice_profile_id = item.get("voice_profile_id")
            if voice_profile_id is not None:
                profile = _get_by_id(VoiceProfile, voice_profile_id)
                if not voice_profile_belongs_to_current_parent(profile):
                    errors.append(f"reading_session with client_id={item.get('client_id')}: invalid voice_profile_id.")
                    continue
                if profile.status != VOICE_STATUS_READY:
                    errors.append(f"reading_session with client_id={item.get('client_id')}: voice profile is not ready.")
                    continue
                voice_profile_id = profile.id

            started_at, timestamp_error = _parse_sync_datetime(item.get("started_at"), "started_at")
            if timestamp_error:
                errors.append(f"reading_session with client_id={item.get('client_id')}: {timestamp_error}")
                continue
            completed_at, timestamp_error = _parse_sync_datetime(item.get("completed_at"), "completed_at")
            if timestamp_error:
                errors.append(f"reading_session with client_id={item.get('client_id')}: {timestamp_error}")
                continue
            accuracy_score = item.get("accuracy_score")
            if accuracy_score is not None:
                try:
                    accuracy_score = float(accuracy_score)
                except (TypeError, ValueError):
                    errors.append(f"reading_session with client_id={item.get('client_id')}: accuracy_score must be a number.")
                    continue
                if not math.isfinite(accuracy_score) or not 0 <= accuracy_score <= 100:
                    errors.append(f"reading_session with client_id={item.get('client_id')}: accuracy_score must be between 0 and 100.")
                    continue
            progress_log = item.get("progress_log")
            if progress_log is not None and not isinstance(progress_log, list):
                errors.append(f"reading_session with client_id={item.get('client_id')}: progress_log must be an array.")
                continue
            if isinstance(progress_log, list) and len(progress_log) > MAX_PROGRESS_ENTRIES:
                errors.append(
                    f"reading_session with client_id={item.get('client_id')}: "
                    f"progress_log cannot contain more than {MAX_PROGRESS_ENTRIES} entries."
                )
                continue

            session = ReadingSession(
                child_id=child_id,
                book_id=book_id,
                voice_profile_id=voice_profile_id,
                started_at=started_at or utc_now(),
                completed_at=completed_at,
                accuracy_score=accuracy_score,
                progress_log=progress_log,
            )
            db.session.add(session)
            db.session.flush()  # assign session.id without committing
            created_sessions.append(session)
            if item.get("client_id"):
                client_id_to_session[item["client_id"]] = session.id

        # 2. Feedback, resolved against either a synced session_id or a client_id.
        for item in feedback_in:
            if not isinstance(item, dict):
                errors.append("feedback entries must be objects.")
                continue
            session_id = item.get("session_id") or client_id_to_session.get(item.get("session_client_id"))
            if not session_id:
                errors.append("feedback entry: could not resolve its reading session.")
                continue

            session = _get_by_id(ReadingSession, session_id)
            if not session or not child_belongs_to_current_parent(_get_by_id(Child, session.child_id)):
                errors.append("feedback entry: reading session is invalid or unauthorized.")
                continue
            session_id = session.id
            feedback_type = item.get("feedback_type", "tip")
            feedback_text = item.get("feedback_text", "")
            if feedback_type not in FEEDBACK_TYPES:
                errors.append(f"feedback entry: feedback_type must be one of {list(FEEDBACK_TYPES)}.")
                continue
            if not isinstance(feedback_text, str) or not feedback_text.strip():
                errors.append("feedback entry: feedback_text is required.")
                continue
            if len(feedback_text.strip()) > MAX_FEEDBACK_TEXT_LENGTH:
                errors.append(
                    f"feedback entry: feedback_text must be "
                    f"{MAX_FEEDBACK_TEXT_LENGTH} characters or fewer."
                )
                continue

            feedback = Feedback(
                session_id=session_id,
                feedback_text=feedback_text.strip(),
                feedback_type=feedback_type,
            )
            db.session.add(feedback)
            created_feedback.append(feedback)

        # 3. Game results, awarding leaderboard points as usual.
        for item in game_results_in:
            if not isinstance(item, dict):
                errors.append("game_result entries must be objects.")
                continue
            child_id = item.get("child_id")
            child = _get_by_id(Child, child_id)
            if not child_id or not child_belongs_to_current_parent(child):
                errors.append("game_result entry: invalid or unauthorized child_id.")
                continue
            child_id = child.id

            game_id = item.get("game_id")
            game = _get_by_id(MiniGame, game_id)
            if not game:
                errors.append("game_result entry: invalid game_id.")
                continue
            game_id = game.id
            try:
                if isinstance(item.get("score"), bool) or isinstance(item.get("score"), float) and not item["score"].is_integer():
                    raise ValueError
                score = int(item.get("score", 0))
            except (TypeError, ValueError):
                errors.append("game_result entry: score must be a whole number.")
                continue
            if score < 0:
                errors.append("game_result entry: score cannot be negative.")
                continue
            maximum_score = _maximum_game_score(game)
            if maximum_score is not None and score > maximum_score:
                errors.append(f"game_result entry: score cannot be greater than {maximum_score}.")
                continue
            completed_at, timestamp_error = _parse_sync_datetime(item.get("completed_at"), "completed_at")
            if timestamp_error:
                errors.append(f"game_result entry: {timestamp_error}")
                continue
            result = GameResult(
                child_id=child_id,
                game_id=game_id,
                score=score,
                completed_at=completed_at or utc_now(),
            )
            db.session.add(result)
            _award_leaderboard_points(child_id, score)
            created_results.append(result)

        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred while syncing."}), 500

    return jsonify(
        {
            "message": "Sync complete.",
            "synced": {
                "reading_sessions": len(created_sessions),
                "feedback": len(created_feedback),
                "game_results": len(created_results),
            },
            "reading_session_ids": {cid: sid for cid, sid in client_id_to_session.items()},
            "errors": errors,
        }
    ), 200
