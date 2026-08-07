from app.extensions import db
from app.utils import utc_isoformat, utc_now


PURPOSE_EMAIL_VERIFICATION = "email_verification"


class EmailVerificationToken(db.Model):
    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        db.Index("ix_email_verification_tokens_account_purpose", "account_id", "purpose"),
        db.Index("ix_email_verification_tokens_token_hash", "token_hash"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    account_id = db.Column(db.Integer, db.ForeignKey("parents.id", ondelete="CASCADE"), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    purpose = db.Column(db.String(50), nullable=False, default=PURPOSE_EMAIL_VERIFICATION)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    request_ip_hash = db.Column(db.String(80), nullable=True)
    user_agent_hash = db.Column(db.String(80), nullable=True)

    account = db.relationship("Parent")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "purpose": self.purpose,
            "expires_at": utc_isoformat(self.expires_at),
            "used_at": utc_isoformat(self.used_at),
            "revoked_at": utc_isoformat(self.revoked_at),
            "created_at": utc_isoformat(self.created_at),
        }
