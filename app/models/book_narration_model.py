from app.extensions import db
from app.utils import utc_isoformat, utc_now


STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
VALID_STATUSES = (STATUS_PROCESSING, STATUS_READY, STATUS_FAILED)


class BookNarration(db.Model):
    """A cached private narration for one book and one voice profile."""

    __tablename__ = "book_narrations"
    __table_args__ = (
        db.Index("ix_book_narrations_book_voice", "book_id", "voice_profile_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    voice_profile_id = db.Column(db.Integer, db.ForeignKey("voice_profiles.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PROCESSING)
    narration_audio_url = db.Column(db.String(500), nullable=True)
    cloudinary_public_id = db.Column(db.String(255), nullable=True, unique=True)
    error_message = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "book_id": self.book_id,
            "voice_profile_id": self.voice_profile_id,
            "status": self.status,
            "created_at": utc_isoformat(self.created_at),
            "error_message": self.error_message if self.status == STATUS_FAILED else None,
        }
