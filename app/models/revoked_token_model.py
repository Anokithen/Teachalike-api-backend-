"""Persistent JWT revocation records."""

from app.extensions import db
from app.utils import utc_now


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    jti = db.Column(db.String(64), nullable=False, unique=True, index=True)
    token_type = db.Column(db.String(16), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    revoked_at = db.Column(db.DateTime, nullable=False, default=utc_now)
