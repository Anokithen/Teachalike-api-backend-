from app.extensions import db
from app.utils import utc_isoformat, utc_now


class Book(db.Model):
    __tablename__ = "books"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    age_group = db.Column(db.String(50), nullable=False)
    reading_level = db.Column(db.String(50), nullable=True)
    content_url = db.Column(db.String(500), nullable=True)
    cover_image_url = db.Column(db.String(500), nullable=True)
    video_url = db.Column(db.String(500), nullable=True)
    image_urls = db.Column(db.JSON, nullable=True)
    text_content = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    mini_games = db.relationship(
        "MiniGame", backref="book", cascade="all, delete-orphan", lazy=True
    )
    reading_sessions = db.relationship("ReadingSession", backref="book", lazy=True)
    narrations = db.relationship(
        "BookNarration", backref="book", cascade="all, delete-orphan", lazy=True
    )

    def to_dict(self, include_content=False):
        data = {
            "id": self.id,
            "title": self.title,
            "age_group": self.age_group,
            "reading_level": self.reading_level,
            "content_url": self.content_url,
            "cover_image_url": self.cover_image_url,
            "video_url": self.video_url,
            "image_urls": self.image_urls or [],
            "created_at": utc_isoformat(self.created_at),
        }
        if include_content:
            data["text_content"] = self.text_content
        return data
