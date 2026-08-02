from flask import current_app, jsonify, request
from flask_jwt_extended import current_user
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.book_model import Book
from app.models.book_like_model import BookLike
from app.services.book_games import ensure_book_games
from app.models.book_view_model import BookView
from app.models.child_model import Child
from app.models.reading_session_model import ReadingSession
from app.middleware import can_access_child
from app.utils import utc_now


def list_books():
    query = Book.query

    age_group = request.args.get("age_group")
    if age_group:
        query = query.filter_by(age_group=age_group)

    reading_level = request.args.get("reading_level")
    if reading_level:
        query = query.filter_by(reading_level=reading_level)

    books = query.order_by(Book.id.asc()).all()
    return jsonify({"books": [b.to_dict() for b in books]}), 200


def get_book(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    # Opening a legacy book prepares its standard games once. A matching
    # ready/fallback/failed fingerprint is reused on every later open.
    try:
        ensure_book_games(book.id, config=current_app.config)
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Legacy mini-game preparation failed safely for book_id=%s", book.id
        )
    return jsonify({"book": book.to_dict(include_content=True)}), 200


def download_book(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404

    package = {
        "book": book.to_dict(include_content=True),
        "assets": {
            "content_url": book.content_url,
        },
    }
    return jsonify(package), 200


def record_view(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    if current_user.is_admin:
        return jsonify({"book_id": book.id, "recorded": False}), 200

    viewed_on = utc_now().date()
    existing = BookView.query.filter_by(
        book_id=book.id,
        account_id=current_user.id,
        viewed_on=viewed_on,
    ).first()
    if existing:
        return jsonify({"book_id": book.id, "recorded": False}), 200
    try:
        db.session.add(
            BookView(
                book_id=book.id,
                account_id=current_user.id,
                viewed_on=viewed_on,
            )
        )
        db.session.commit()
        return jsonify({"book_id": book.id, "recorded": True}), 201
    except IntegrityError:
        # A concurrent duplicate request is equivalent to an already-recorded view.
        db.session.rollback()
        duplicate = BookView.query.filter_by(
            book_id=book.id,
            account_id=current_user.id,
            viewed_on=viewed_on,
        ).first()
        if duplicate:
            return jsonify({"book_id": book.id, "recorded": False}), 200
        return jsonify({"error": "The book view conflicted with another update."}), 409
    except Exception:
        db.session.rollback()
        return jsonify({"error": "The book view could not be recorded."}), 500


def _accessible_child(child_id):
    child = db.session.get(Child, child_id)
    if child is None:
        return None, (jsonify({"error": "Child not found."}), 404)
    if not can_access_child(child):
        return None, (jsonify({"error": "You cannot access this child."}), 403)
    return child, None


def like_book(book_id, child_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    child, error = _accessible_child(child_id)
    if error:
        return error
    existing = BookLike.query.filter_by(book_id=book.id, child_id=child.id).first()
    if existing:
        return jsonify({"book_id": book.id, "child_id": child.id, "liked": True}), 200
    try:
        db.session.add(BookLike(book_id=book.id, child_id=child.id))
        db.session.commit()
        return jsonify({"book_id": book.id, "child_id": child.id, "liked": True}), 201
    except IntegrityError:
        db.session.rollback()
        duplicate = BookLike.query.filter_by(
            book_id=book.id, child_id=child.id
        ).first()
        if duplicate:
            return jsonify({"book_id": book.id, "child_id": child.id, "liked": True}), 200
        return jsonify({"error": "The book like conflicted with another update."}), 409
    except Exception:
        db.session.rollback()
        return jsonify({"error": "The book could not be liked."}), 500


def unlike_book(book_id, child_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    child, error = _accessible_child(child_id)
    if error:
        return error
    like = BookLike.query.filter_by(book_id=book.id, child_id=child.id).first()
    if like is None:
        return jsonify({"book_id": book.id, "child_id": child.id, "liked": False}), 200
    try:
        db.session.delete(like)
        db.session.commit()
        return jsonify({"book_id": book.id, "child_id": child.id, "liked": False}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "The book like could not be removed."}), 500


def get_engagement(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404

    child = None
    raw_child_id = request.args.get("child_id")
    if raw_child_id is not None:
        try:
            child_id = int(raw_child_id)
        except (TypeError, ValueError):
            return jsonify({"error": "child_id must be a positive integer."}), 400
        if child_id <= 0:
            return jsonify({"error": "child_id must be a positive integer."}), 400
        child, error = _accessible_child(child_id)
        if error:
            return error

    view_stats = db.session.query(
        func.count(BookView.id),
        func.count(func.distinct(BookView.account_id)),
    ).filter(BookView.book_id == book.id).one()
    read_stats = db.session.query(
        func.count(ReadingSession.id),
        func.sum(case((ReadingSession.completed_at.isnot(None), 1), else_=0)),
        func.count(func.distinct(ReadingSession.child_id)),
    ).filter(ReadingSession.book_id == book.id).one()
    like_count = db.session.query(func.count(BookLike.id)).filter(
        BookLike.book_id == book.id
    ).scalar()
    payload = {
        "book_id": book.id,
        "total_views": int(view_stats[0] or 0),
        "unique_viewers": int(view_stats[1] or 0),
        "total_reads": int(read_stats[0] or 0),
        "completed_reads": int(read_stats[1] or 0),
        "unique_readers": int(read_stats[2] or 0),
        "likes": int(like_count or 0),
    }
    if child is not None:
        payload["liked_by_child"] = BookLike.query.filter_by(
            book_id=book.id, child_id=child.id
        ).first() is not None
    return jsonify(payload), 200
