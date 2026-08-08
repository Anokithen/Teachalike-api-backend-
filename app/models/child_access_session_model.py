from app.extensions import db
from app.utils import utc_isoformat, utc_now

class ChildAccessSession(db.Model):
    __tablename__ = "child_access_sessions"
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parents.id", ondelete="CASCADE"), nullable=False, index=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    child_access_version = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    last_used_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    revoked_at = db.Column(db.DateTime, index=True)
    revoke_reason = db.Column(db.String(80))
    child = db.relationship("Child")
    def to_dict(self):
        return {"id": self.child.id, "name": self.child.name, "age": self.child.age, "profile_image_url": self.child.profile_image_url, "reading_level": self.child.reading_level}
    def expiry(self):
        return utc_isoformat(self.expires_at)
