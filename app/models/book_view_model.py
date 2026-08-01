"""Daily authenticated book-view events."""

from app.extensions import db
from app.utils import utc_now


class BookView(db.Model):
    __tablename__ = "book_views"
    __table_args__ = (
        db.UniqueConstraint(
            "book_id", "account_id", "viewed_on", name="uq_book_views_daily"
        ),
        db.Index("ix_book_views_book_id", "book_id"),
        db.Index("ix_book_views_account_id", "account_id"),
        db.Index("ix_book_views_viewed_on", "viewed_on"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    book_id = db.Column(
        db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    account_id = db.Column(
        db.Integer, db.ForeignKey("parents.id", ondelete="CASCADE"), nullable=False
    )
    viewed_on = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

