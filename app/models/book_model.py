from app.extensions import db
from app.utils import utc_isoformat, utc_now


class Book(db.Model):
    __tablename__ = "books"
    __table_args__ = (
        db.UniqueConstraint(
            "created_by_account_id",
            "creation_request_id",
            name="uq_books_creator_request",
        ),
        db.Index("ix_books_created_by_account_id", "created_by_account_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    age_group = db.Column(db.String(50), nullable=False)
    reading_level = db.Column(db.String(50), nullable=True)
    content_url = db.Column(db.String(500), nullable=True)
    cover_image_url = db.Column(db.String(500), nullable=True)
    video_url = db.Column(db.String(500), nullable=True)
    image_urls = db.Column(db.JSON, nullable=True)
    text_content = db.Column(db.Text, nullable=True)
    created_by_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "parents.id",
            ondelete="SET NULL",
            name="fk_books_created_by_account",
        ),
        nullable=True,
    )
    creator_name_snapshot = db.Column(db.String(120), nullable=True)
    creation_request_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    creator = db.relationship(
        "Parent",
        foreign_keys=[created_by_account_id],
        back_populates="created_books",
    )

    mini_games = db.relationship(
        "MiniGame", backref="book", cascade="all, delete-orphan", lazy=True
    )
    reading_sessions = db.relationship("ReadingSession", backref="book", lazy=True)
    narrations = db.relationship(
        "BookNarration", backref="book", cascade="all, delete-orphan", lazy=True
    )
    views = db.relationship(
        "BookView", backref="book", cascade="all, delete-orphan", lazy=True
    )
    likes = db.relationship(
        "BookLike", backref="book", cascade="all, delete-orphan", lazy=True
    )

    def to_dict(self, include_content=False):
        creator = self.creator
        is_teacher_creator = creator is not None and creator.role == "teacher"
        creator_name = creator.name if is_teacher_creator else self.creator_name_snapshot
        created_by = None
        if creator_name:
            created_by = {
                "account_id": creator.id if is_teacher_creator else None,
                "name": creator_name,
                "role": "teacher",
            }
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "age_group": self.age_group,
            "reading_level": self.reading_level,
            "content_url": self.content_url,
            "cover_image_url": self.cover_image_url,
            "video_url": self.video_url,
            "image_urls": self.image_urls or [],
            "created_at": utc_isoformat(self.created_at),
            "updated_at": utc_isoformat(self.updated_at),
            "created_by": created_by,
            "created_by_label": (
                f"Created by {creator_name}"
                if creator_name
                else "Created by TeachAlike"
            ),
        }
        if include_content:
            data["text_content"] = self.text_content
        return data
