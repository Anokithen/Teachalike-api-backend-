from app.extensions import db
from app.utils import utc_isoformat, utc_now


EMAIL_STATUS_PENDING = "pending"
EMAIL_STATUS_SENDING = "sending"
EMAIL_STATUS_SENT = "sent"
EMAIL_STATUS_RETRY = "retry"
EMAIL_STATUS_FAILED = "failed"
EMAIL_STATUS_CANCELLED = "cancelled"

EMAIL_TYPE_VERIFY_ACCOUNT = "verify_account"
EMAIL_TYPE_TEACHER_APPROVED = "teacher_approved"


class EmailDelivery(db.Model):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        db.UniqueConstraint("event_key", name="uq_email_deliveries_event_key"),
        db.Index("ix_email_deliveries_status_next_attempt", "status", "next_attempt_at"),
        db.Index("ix_email_deliveries_recipient_account_id", "recipient_account_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    recipient_account_id = db.Column(db.Integer, db.ForeignKey("parents.id", ondelete="SET NULL"), nullable=True)
    recipient_email = db.Column(db.String(120), nullable=False)
    email_type = db.Column(db.String(50), nullable=False)
    event_key = db.Column(db.String(190), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=EMAIL_STATUS_PENDING)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime, nullable=True)
    provider_message_id = db.Column(db.String(255), nullable=True)
    last_error_code = db.Column(db.String(80), nullable=True)
    context_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    sent_at = db.Column(db.DateTime, nullable=True)

    recipient_account = db.relationship("Parent")

    def to_dict(self):
        return {
            "id": self.id,
            "recipient_email": self.recipient_email,
            "email_type": self.email_type,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "next_attempt_at": utc_isoformat(self.next_attempt_at),
            "provider_message_id": self.provider_message_id,
            "last_error_code": self.last_error_code,
            "created_at": utc_isoformat(self.created_at),
            "sent_at": utc_isoformat(self.sent_at),
        }
