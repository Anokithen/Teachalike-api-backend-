from datetime import date, timedelta

from flask import jsonify, request

from app.extensions import db
from app.utils import utc_now
from app.models.child_model import Child
from app.models.leaderboard_model import LeaderboardEntry
from app.middleware import child_belongs_to_current_parent


def _current_week_start():
    today = utc_now().date()
    return today - timedelta(days=today.weekday())


def _resolve_week(week_param):
    if not week_param or week_param == "current":
        return _current_week_start()
    try:
        return date.fromisoformat(week_param)
    except ValueError:
        return _current_week_start()


def get_leaderboard():
    """GET /api/leaderboard?scope=friends&week=current

    NOTE: the ERD/proposal describes a "friends" scope, but no friends/classmates
    relationship exists yet in the schema. Until that relationship is added,
    `scope` is accepted but every scope currently ranks across all children.
    """
    week_start = _resolve_week(request.args.get("week"))

    entries = (
        LeaderboardEntry.query.filter_by(week_start=week_start)
        .order_by(LeaderboardEntry.points.desc())
        .order_by(LeaderboardEntry.id.asc())
        .all()
    )

    ranked = [entry.to_dict(rank=i + 1) for i, entry in enumerate(entries)]
    return jsonify({"week_start": week_start.isoformat(), "leaderboard": ranked}), 200
