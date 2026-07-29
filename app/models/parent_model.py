from app.extensions import db
from app.utils import utc_isoformat, utc_now
from werkzeug.security import generate_password_hash, check_password_hash

ROLE_PARENT = "parent"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"

VALID_ROLES = (ROLE_PARENT, ROLE_TEACHER, ROLE_ADMIN)


class Parent(db.Model):
    

    __tablename__ = "parents"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_PARENT)
    is_banned = db.Column(db.Boolean, nullable=False, default=False)
    profile_image_url = db.Column(db.String(500), nullable=True)
    profile_image_public_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    children = db.relationship(
        "Child",
        foreign_keys="Child.parent_id",
        backref="parent",
        cascade="all, delete-orphan",
        lazy=True,
    )
    voice_profiles = db.relationship(
        "VoiceProfile", backref="parent", cascade="all, delete-orphan", lazy=True
    )

    def set_password(self, password):
        self.password = generate_password_hash(password)
