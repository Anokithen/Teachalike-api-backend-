from app.extensions import db
from app.utils import utc_now


class LeaderboardEntry(db.Model):
    __tablename__ = "leaderboard_entries"
    __table_args__ = (
        db.UniqueConstraint("child_id", "week_start", name="uq_child_week"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False)
    points = db.Column(db.Integer, nullable=False, default=0)
    streak_count = db.Column(db.Integer, nullable=False, default=0)
    # This column stores a calendar date, not a timezone-aware datetime.
    week_start = db.Column(db.Date, nullable=False, default=lambda: utc_now().date())

    def to_dict(self, rank=None):
        data = {
            "id": self.id,
            "child_id": self.child_id,
            "child_name": self.child.name if self.child else None,
            "points": self.points,
            "streak_count": self.streak_count,
            "week_start": self.week_start.isoformat() if self.week_start else None,
        }
        if rank is not None:
            data["rank"] = rank
        return data
