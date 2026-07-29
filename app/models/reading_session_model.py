from app.extensions import db
from app.utils import utc_isoformat, utc_now


class ReadingSession(db.Model):
    __tablename__ = "reading_sessions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    voice_profile_id = db.Column(
        db.Integer, db.ForeignKey("voice_profiles.id"), nullable=True
    )
    started_at = db.Column(db.DateTime, default=utc_now)
    completed_at = db.Column(db.DateTime, nullable=True)
    accuracy_score = db.Column(db.Float, nullable=True)
    progress_log = db.Column(db.JSON, nullable=True)  # per-line accuracy entries

    feedback_entries = db.relationship(
        "Feedback", backref="session", cascade="all, delete-orphan", lazy=True
    )

    def to_dict(self):
        return {
            "id": self.id,
            "child_id": self.child_id,
            "book_id": self.book_id,
            "voice_profile_id": self.voice_profile_id,
            "started_at": utc_isoformat(self.started_at),
            "completed_at": utc_isoformat(self.completed_at),
            "accuracy_score": self.accuracy_score,
            "progress_log": self.progress_log or [],
            "is_complete": self.completed_at is not None,
        }
