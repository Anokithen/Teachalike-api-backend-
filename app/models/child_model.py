from app.extensions import db
from app.utils import utc_isoformat, utc_now
from werkzeug.security import check_password_hash, generate_password_hash


class Child(db.Model):
    __tablename__ = "children"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parents.id"), nullable=False)
    # Who actually created the record: the parent themself, or a teacher
    # adding a child on the parent's behalf. Nullable for pre-existing rows.
    created_by_id = db.Column(db.Integer, db.ForeignKey("parents.id"), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(30), nullable=False, default="prefer_not_to_say")
    child_pin_hash = db.Column(db.String(255), nullable=True)
    profile_image_url = db.Column(db.String(500), nullable=True)
    profile_image_public_id = db.Column(db.String(255), nullable=True)
    reading_level = db.Column(db.String(50), nullable=False, default="beginner")
    created_at = db.Column(db.DateTime, default=utc_now)

    # Keep this nullable creator link intact when a teacher account is
    # removed. SQLAlchemy will set created_by_id to NULL instead of allowing
    # the foreign-key constraint to prevent teacher deletion.
    created_by = db.relationship(
        "Parent",
        foreign_keys=[created_by_id],
        backref="created_children",
    )

    reading_sessions = db.relationship(
        "ReadingSession", backref="child", cascade="all, delete-orphan", lazy=True
    )
    game_results = db.relationship(
        "GameResult", backref="child", cascade="all, delete-orphan", lazy=True
    )
    leaderboard_entries = db.relationship(
        "LeaderboardEntry", backref="child", cascade="all, delete-orphan", lazy=True
    )

    def to_dict(self):
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "created_by_id": self.created_by_id,
            "name": self.name,
            "age": self.age,
            "gender": self.gender,
            "profile_image_url": self.profile_image_url,
            "has_pin": bool(self.child_pin_hash),
            "reading_level": self.reading_level,
            "created_at": utc_isoformat(self.created_at),
        }

    def set_pin(self, pin):
        self.child_pin_hash = generate_password_hash(pin)

    def check_pin(self, pin):
        return bool(self.child_pin_hash) and check_password_hash(self.child_pin_hash, pin)
