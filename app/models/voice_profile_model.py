from app.extensions import db
from app.utils import utc_isoformat, utc_now

STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

VALID_STATUSES = (STATUS_PROCESSING, STATUS_READY, STATUS_FAILED)


class VoiceProfile(db.Model):
    __tablename__ = "voice_profiles"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parents.id"), nullable=False)
    label = db.Column(db.String(80), nullable=True)  # e.g. "Mum's voice"
    voice_sample_url = db.Column(db.String(500), nullable=False)
    cloudinary_public_id = db.Column(db.String(255), nullable=True, unique=True)
    elevenlabs_voice_id = db.Column(db.String(255), nullable=True, unique=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PROCESSING)
    created_at = db.Column(db.DateTime, default=utc_now)

    reading_sessions = db.relationship(
        "ReadingSession", backref="voice_profile", lazy=True
    )
    narrations = db.relationship(
        "BookNarration", backref="voice_profile", cascade="all, delete-orphan", lazy=True
    )

    def to_dict(self):
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "label": self.label,
            "voice_sample_url": self.voice_sample_url,
            "has_cloned_voice": bool(self.elevenlabs_voice_id),
            "owner_name": self.parent.name if self.parent else None,
            "status": self.status,
            "created_at": utc_isoformat(self.created_at),
        }
