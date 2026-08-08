"""Authoritative mini-game grading and leaderboard awards."""

from datetime import timedelta
import unicodedata

from flask import g, jsonify, request
from flask_jwt_extended import current_user

from app.extensions import db
from app.utils import utc_now
from app.models.child_model import Child
from app.models.mini_game_model import MiniGame
from app.models.game_result_model import GameResult
from app.models.leaderboard_model import LeaderboardEntry
from app.middleware import child_belongs_to_current_parent


SUPPORTED_GAMES = {"quiz", "word_puzzle", "spelling"}
SPELLING_WORD_COUNTS = {"easy": 3, "medium": 6, "hard": 10}


def _current_week_start():
    today = utc_now().date()
    return today - timedelta(days=today.weekday())


def _award_leaderboard_points(child_id, points):
    week_start = _current_week_start()
    entry = LeaderboardEntry.query.filter_by(child_id=child_id, week_start=week_start).first()
    if not entry:
        entry = LeaderboardEntry(child_id=child_id, week_start=week_start, points=0, streak_count=0)
        db.session.add(entry)
    entry.points += points
    return entry


def _content_items(game):
    content = game.content if isinstance(game.content, dict) else {}
    key = "questions" if game.game_type == "quiz" else "words"
    raw_items = content.get(key)
    return raw_items if isinstance(raw_items, list) else []


def _maximum_game_score(game):
    items = _content_items(game)
    if game.game_type in SUPPORTED_GAMES:
        return min(len(items), 10) * 10
    content = game.content if isinstance(game.content, dict) else {}
    configured_maximum = content.get("max_score")
    if isinstance(configured_maximum, int) and not isinstance(configured_maximum, bool) and 0 <= configured_maximum <= 1000:
        return configured_maximum
    return 100


def _normalise_answer(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _authoritative_items(game, difficulty=None):
    raw_items = _content_items(game)
    items = []
    prefix = "q" if game.game_type == "quiz" else "w"
    for index, raw in enumerate(raw_items):
        item = raw if isinstance(raw, dict) else {"word": raw}
        items.append({**item, "id": str(item.get("id") or f"{prefix}_{index + 1:02d}")})
    if game.game_type == "spelling":
        if difficulty not in SPELLING_WORD_COUNTS:
            raise ValueError("difficulty must be easy, medium, or hard for spelling.")
        return items[:min(SPELLING_WORD_COUNTS[difficulty], len(items))]
    return items[:10]


def grade_game_answers(game, answers, *, difficulty=None):
    """Grade supported game answers using only authoritative stored content."""
    if game.game_type not in SUPPORTED_GAMES:
        raise ValueError("This game does not support answer-based grading.")
    if game.generation_status not in {"ready", "fallback"}:
        raise ValueError("This game is not ready to grade yet.")
    if not isinstance(answers, list) or not answers:
        raise ValueError("answers must be a non-empty list.")
    authoritative = _authoritative_items(game, difficulty)
    if len(answers) != len(authoritative):
        raise ValueError("Submit one answer for every question in this activity.")
    by_id = {item["id"]: item for item in authoritative}
    submitted_ids = []
    answer_results = []
    score = 0
    correct_count = 0
    stored_answers = []

    for raw_answer in answers:
        if not isinstance(raw_answer, dict):
            raise ValueError("Each answer must be an object.")
        if {"score", "points", "correct", "correct_answer", "correct_option_index"}.intersection(raw_answer):
            raise ValueError("Answer keys and scores are calculated by the server.")
        question_id = str(raw_answer.get("question_id") or raw_answer.get("word_id") or "").strip()
        if not question_id or question_id not in by_id or question_id in submitted_ids:
            raise ValueError("Each answer must reference one unique activity item ID.")
        submitted_ids.append(question_id)
        item = by_id[question_id]

        if game.game_type == "quiz":
            selected_index = raw_answer.get("selected_option_index")
            if isinstance(selected_index, bool) or not isinstance(selected_index, int) or not 0 <= selected_index <= 3:
                raise ValueError("selected_option_index must be between 0 and 3.")
            hint_used = raw_answer.get("hint_used", False)
            if not isinstance(hint_used, bool):
                raise ValueError("hint_used must be true or false.")
            correct_index = item.get("correct_option_index")
            correct = selected_index == correct_index
            if correct:
                correct_count += 1
                score += 5 if hint_used else 10
            answer_results.append({
                "question_id": question_id,
                "correct": correct,
                "correct_option_index": correct_index,
                "explanation": str(item.get("explanation") or "That answer matches the story."),
            })
            stored_answers.append({
                "question_id": question_id,
                "selected_option_index": selected_index,
                "hint_used": hint_used,
                "correct": correct,
            })
        else:
            response = raw_answer.get("response")
            if not isinstance(response, str) or not response.strip() or len(response) > 100:
                raise ValueError("Each word response must be between 1 and 100 characters.")
            expected_word = str(item.get("word") or "")
            correct = _normalise_answer(response) == _normalise_answer(expected_word)
            if correct:
                correct_count += 1
                score += 10
            answer_results.append({
                "question_id": question_id,
                "correct": correct,
                "correct_answer": expected_word,
                "explanation": (
                    "Great spelling!" if correct else f"The story word is “{expected_word}”."
                ),
            })
            stored_answers.append({
                "question_id": question_id,
                "response": response.strip(),
                "correct": correct,
            })

    maximum = len(authoritative) * 10
    return {
        "score": min(score, maximum),
        "correct_answers": correct_count,
        "total_questions": len(authoritative),
        "answers": answer_results,
        "stored_answers": stored_answers,
    }


def _validated_child(data):
    child_id = data.get("child_id")
    if isinstance(child_id, bool) or not isinstance(child_id, (int, str)):
        return None
    try:
        child_id = int(child_id)
    except (TypeError, ValueError):
        return None
    child = db.session.get(Child, child_id) if child_id > 0 else None
    return child if child_belongs_to_current_parent(child) else None


def submit_game_result(game_id):
    game = db.session.get(MiniGame, game_id)
    if not game:
        return jsonify({"error": "Mini-game not found."}), 404
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required."}), 400
    if current_user.role == "parent":
        if "child_id" in data: return jsonify({"error":"Parent results cannot override the active child.","error_code":"ACTIVE_CHILD_MISMATCH"}), 409
        if "score" in data: return jsonify({"error":"Scores are calculated by the server."}), 400
        child = g.active_child
    else: child = _validated_child(data)
    if child is None:
        return jsonify({"errors": ["A valid child_id belonging to this account is required."]}), 400

    if game.game_type in SUPPORTED_GAMES:
        forbidden = {"score", "points", "correct_answer", "correct_option_index", "book_text"}
        if forbidden.intersection(data):
            return jsonify({"error": "Scores and answer keys are calculated by the server."}), 400
        try:
            grading = grade_game_answers(
                game, data.get("answers"), difficulty=data.get("difficulty")
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        score = grading["score"]
    else:
        try:
            score_value = data.get("score")
            if isinstance(score_value, bool) or isinstance(score_value, float) and not score_value.is_integer():
                raise ValueError
            score = int(score_value)
        except (TypeError, ValueError):
            return jsonify({"errors": ["score must be a whole number."]}), 400
        maximum = _maximum_game_score(game)
        if score < 0 or score > maximum:
            return jsonify({"error": f"score must be between 0 and {maximum}."}), 400
        grading = {
            "correct_answers": None,
            "total_questions": None,
            "answers": [],
            "stored_answers": None,
        }

    try:
        result = GameResult(
            child_id=child.id,
            game_id=game.id,
            score=score,
            correct_answers=grading["correct_answers"],
            total_questions=grading["total_questions"],
            answers_data=grading["stored_answers"],
            game_content_version=game.content_version,
            points_awarded=score,
            completed_at=utc_now(),
        )
        db.session.add(result)
        _award_leaderboard_points(child.id, score)
        db.session.commit()
        return jsonify({
            "message": f"Great work! {score} points were added to {child.name}.",
            "game_result": result.to_dict(),
            "answers": grading["answers"],
        }), 201
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def list_child_game_results(child_id):
    child = db.session.get(Child, child_id)
    if not child_belongs_to_current_parent(child):
        return jsonify({"error": "Child not found."}), 404
    results = GameResult.query.filter_by(child_id=child_id).order_by(
        GameResult.completed_at.desc()
    ).all()
    return jsonify({"game_results": [result.to_dict() for result in results]}), 200
