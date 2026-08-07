from app.extensions import db
from app.utils import utc_isoformat, utc_now


class AccountIdentity(db.Model):
    __tablename__ = "account_identities"
    __table_args__ = (
        db.UniqueConstraint("provider", "provider_subject", name="uq_account_identity_provider_subject"),
        db.Index("ix_account_identities_account_id", "account_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    account_id = db.Column(db.Integer, db.ForeignKey("parents.id", ondelete="CASCADE"), nullable=False)
    provider = db.Column(db.String(30), nullable=False)
    provider_subject = db.Column(db.String(255), nullable=False)
    provider_email = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    account = db.relationship("Parent", back_populates="identities")

    def to_dict(self):
        return {
            "provider": self.provider,
            "provider_email": self.provider_email,
            "created_at": utc_isoformat(self.created_at),
        }
