from app.extensions import db
from app.utils import utc_isoformat, utc_now


class PronunciationAttempt(db.Model):
    __tablename__ = "pronunciation_attempts"
    __table_args__ = (
        db.Index("ix_pronunciation_attempts_session_paragraph", "reading_session_id", "paragraph_index"),
        db.Index("ix_pronunciation_attempts_created_at", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    reading_session_id = db.Column(
        db.Integer,
        db.ForeignKey("reading_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paragraph_index = db.Column(db.Integer, nullable=False, index=True)
    original_text = db.Column(db.Text, nullable=False)
    spoken_transcript = db.Column(db.Text, nullable=False)
    provider_accuracy = db.Column(db.Float, nullable=True)
    text_match_accuracy = db.Column(db.Float, nullable=False)
    correct_word_count = db.Column(db.Integer, nullable=False)
    substitution_count = db.Column(db.Integer, nullable=False)
    deletion_count = db.Column(db.Integer, nullable=False)
    insertion_count = db.Column(db.Integer, nullable=False)
    comparison_data = db.Column(db.JSON, nullable=False)
    scoring_provider = db.Column(db.String(50), nullable=False)
    scoring_model = db.Column(db.String(200), nullable=True)
    points_awarded = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "reading_session_id": self.reading_session_id,
            "paragraph_index": self.paragraph_index,
            "original_text": self.original_text,
            "spoken_transcript": self.spoken_transcript,
            "provider_accuracy": (
                round(self.provider_accuracy)
                if self.provider_accuracy is not None
                else None
            ),
            "text_match_accuracy": round(self.text_match_accuracy),
            "correct_word_count": self.correct_word_count,
            "substitution_count": self.substitution_count,
            "deletion_count": self.deletion_count,
            "insertion_count": self.insertion_count,
            "comparison": self.comparison_data,
            "scoring_provider": self.scoring_provider,
            "scoring_model": self.scoring_model,
            "points_awarded": self.points_awarded,
            "created_at": utc_isoformat(self.created_at),
        }
