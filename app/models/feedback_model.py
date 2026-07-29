from app.extensions import db
from app.utils import utc_isoformat, utc_now

FEEDBACK_TYPES = ("praise", "correction", "tip")


class Feedback(db.Model):
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("reading_sessions.id"), nullable=False
    )
    feedback_text = db.Column(db.Text, nullable=False)
    feedback_type = db.Column(db.String(20), nullable=False, default="tip")
    audio_url = db.Column(db.String(500), nullable=True)  # cloned-voice audio
    created_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "feedback_text": self.feedback_text,
            "feedback_type": self.feedback_type,
            "audio_url": self.audio_url,
            "created_at": utc_isoformat(self.created_at),
        }
