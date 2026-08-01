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
    teacher_profile = db.relationship(
        "TeacherProfile",
        foreign_keys="TeacherProfile.account_id",
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    reviewed_teacher_profiles = db.relationship(
        "TeacherProfile",
        foreign_keys="TeacherProfile.reviewed_by_id",
        back_populates="reviewed_by",
    )
    created_books = db.relationship(
        "Book",
        foreign_keys="Book.created_by_account_id",
        back_populates="creator",
    )

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_teacher(self):
        return self.role == ROLE_TEACHER

    @property
    def is_parent(self):
        return self.role == ROLE_PARENT

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "is_banned": self.is_banned,
            "profile_image_url": self.profile_image_url,
            "created_at": utc_isoformat(self.created_at),
        }

    def to_self_dict(self):
        """Include private teacher fields only for the account itself."""
        data = self.to_dict()
        if self.is_teacher and self.teacher_profile:
            data["teacher_profile"] = self.teacher_profile.to_private_dict()
        return data
