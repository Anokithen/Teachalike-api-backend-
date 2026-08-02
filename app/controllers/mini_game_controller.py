"""Public mini-game reads plus authorized generation management."""

from flask import current_app, jsonify
from flask_jwt_extended import current_user

from app.extensions import db
from app.models.book_model import Book
from app.models.mini_game_model import MiniGame
from app.models.teacher_application_model import APPROVAL_APPROVED
from app.security import mini_game_generation_requests
from app.services.book_games import active_book_games, ensure_book_games


def _can_regenerate(book):
    if current_user is None or current_user.is_banned:
        return False
    if current_user.is_admin:
        return True
    return bool(
        current_user.is_teacher
        and current_user.teacher_application is not None
        and current_user.teacher_application.approval_status == APPROVAL_APPROVED
        and book.created_by_account_id == current_user.id
    )


def _safe_generate(book_id, *, force=False):
    try:
        return ensure_book_games(book_id, force=force, config=current_app.config)
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Mini-game generation failed safely for book_id=%s", book_id
        )
        return active_book_games(book_id), False


def list_book_mini_games(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404

    # A legacy book triggers generation once. Matching fallback/failed hashes
    # are reused, so a public GET never loops provider failures.
    games, _changed = _safe_generate(book.id)
    return jsonify({"mini_games": [game.to_dict() for game in games]}), 200


def get_mini_game(game_id):
    game = db.session.get(MiniGame, game_id)
    if not game:
        return jsonify({"error": "Mini-game not found."}), 404
    if (
        game.generation_status in {None, "pending"}
        or not game.source_content_hash
        or not game.generator_version
    ):
        _safe_generate(game.book_id)
        game = db.session.get(MiniGame, game_id)
    return jsonify({"mini_game": game.to_public_dict()}), 200


def generation_status(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    games = active_book_games(book.id)
    can_manage = _can_regenerate(book)
    records = []
    for game in games:
        record = {
            "id": game.id,
            "game_type": game.game_type,
            "generation_status": game.generation_status,
            "content_version": game.content_version,
            "generated_at": game.to_dict().get("generated_at"),
        }
        if can_manage:
            record.update({
                "generator_provider": game.generator_provider,
                "generator_model": game.generator_model,
                "generation_error": game.generation_error,
                "source_content_hash": game.source_content_hash,
            })
        records.append(record)
    return jsonify({
        "book_id": book.id,
        "can_regenerate": can_manage,
        "mini_games": records,
    }), 200


def regenerate_book_games(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    if not _can_regenerate(book):
        return jsonify({"error": "You do not have permission to regenerate these games."}), 403

    limit = current_app.config["MINI_GAME_REGENERATION_RATE_LIMIT"]
    window = current_app.config["MINI_GAME_REGENERATION_WINDOW_SECONDS"]
    key = f"mini-game-generation:{current_user.id}:{book.id}"
    blocked, retry_after = mini_game_generation_requests.blocked(key, limit, window)
    if blocked:
        response = jsonify({"error": "Please wait before preparing these games again."})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429
    mini_game_generation_requests.record_failure(key, window)

    games, _changed = _safe_generate(book.id, force=True)
    return jsonify({
        "message": "The story games have been prepared.",
        "mini_games": [game.to_dict() for game in games],
    }), 200
