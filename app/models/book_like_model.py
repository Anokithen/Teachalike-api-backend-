"""Child-owned book likes."""

from app.extensions import db
from app.utils import utc_now


class BookLike(db.Model):
    __tablename__ = "book_likes"
    __table_args__ = (
        db.UniqueConstraint("book_id", "child_id", name="uq_book_likes_child"),
        db.Index("ix_book_likes_book_id", "book_id"),
        db.Index("ix_book_likes_child_id", "child_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    book_id = db.Column(
        db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    child_id = db.Column(
        db.Integer, db.ForeignKey("children.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

