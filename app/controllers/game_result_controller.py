from datetime import timedelta

from flask import jsonify, request

from app.extensions import db
from app.utils import utc_now
from app.models.child_model import Child
from app.models.mini_game_model import MiniGame
from app.models.game_result_model import GameResult
from app.models.leaderboard_model import LeaderboardEntry
from app.middleware import child_belongs_to_current_parent


def _current_week_start():
    today = utc_now().date()
    return today - timedelta(days=today.weekday())  # Monday of the current week


def _award_leaderboard_points(child_id, points):
    week_start = _current_week_start()
    entry = LeaderboardEntry.query.filter_by(child_id=child_id, week_start=week_start).first()
    if not entry:
        entry = LeaderboardEntry(child_id=child_id, week_start=week_start, points=0, streak_count=0)
        db.session.add(entry)
    entry.points += points
    return entry


def _maximum_game_score(game):
    """Return the maximum score supported by the built-in game content.

    Scores are submitted by the browser, so the API must enforce a ceiling
    before adding points to the leaderboard. Unknown/custom game types use a
    conservative default rather than allowing arbitrary leaderboard points.
    """
    content = game.content if isinstance(game.content, dict) else {}
    if game.game_type == "quiz":
        questions = content.get("questions")
        return len(questions) * 10 if isinstance(questions, list) else None
    if game.game_type in {"word_puzzle", "spelling"}:
        words = content.get("words")
        return min(len(words), 10) * 10 if isinstance(words, list) else None
    configured_maximum = content.get("max_score")
    if (
        isinstance(configured_maximum, int)
        and not isinstance(configured_maximum, bool)
        and 0 <= configured_maximum <= 1000
    ):
        return configured_maximum
    return 100
