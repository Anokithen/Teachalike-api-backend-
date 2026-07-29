"""Default mini-games generated for each book in the catalog."""
import re
import os

from flask import current_app

from app.extensions import db
from app.models.mini_game_model import MiniGame
from app.services.gemini_service import GeminiError, generate_story_word_quiz


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "the", "this", "that", "to", "was", "with",
}


def _keywords(book):
    """Return a small, predictable set of child-friendly words from a book."""
    source = f"{book.title} {book.text_content or ''}"
    words = re.findall(r"[A-Za-z]{3,}", source.lower())
    selected = []
    for word in words:
        if word not in STOP_WORDS and word not in selected:
            selected.append(word)
        if len(selected) == 8:
            break
    return selected or ["story", "book", "read"]


def _quiz_questions(words, book=None):
    questions = []
    fallback_options = ["story", "reading", "friend", "adventure"]
    for index, word in enumerate(words[:6]):
        options = [word]
        for choice in words + fallback_options:
            if choice not in options:
                options.append(choice)
            if len(options) == 4:
                break
        sentence = next(
            (part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", book.text_content or "") if word in part.lower()),
            None,
        ) if book else None
        question = "Which word appeared in the story?"
        if sentence:
            masked = re.sub(rf"\b{re.escape(word)}\b", "____", sentence, count=1, flags=re.IGNORECASE)
            question = f"Which word completes this story sentence? “{masked}”"
        questions.append({
            "word": word,
            "question": question,
            "options": options,
            "answer": word,
            "hint": "Look for a word that appears in the story.",
            "explanation": f"“{word}” is one of the important words from this story.",
        })
    return questions


QUIZ_GENERATOR = "gemini-story-quiz-v2"


def _runtime_config(config=None):
    if config is not None:
        return config
    try:
        return current_app.config
    except RuntimeError:
        return os.environ


def _has_gemini_key(config):
    return bool(str(config.get("GEMINI_API_KEY") or config.get("GOOGLE_API_KEY") or "").strip())


def _fallback_quiz_content(words, book=None):
    return {
        "questions": _quiz_questions(words, book),
        "generator": "fallback",
        "generator_version": "fallback-v1",
    }


def _build_quiz_content(book, words, config=None):
    config = _runtime_config(config)
    if _has_gemini_key(config):
        try:
            return {
                "questions": generate_story_word_quiz(book, config),
                "generator": "gemini",
                "generator_version": QUIZ_GENERATOR,
            }
        except GeminiError as exc:
            try:
                current_app.logger.warning("Gemini quiz generation failed for book %s: %s", book.id, exc)
            except RuntimeError:
                pass
    return _fallback_quiz_content(words, book)


def ensure_story_quiz(book, quiz_game=None, config=None):
    """Create or upgrade one book's quiz, returning whether it changed."""
    words = _keywords(book)
    quiz_game = quiz_game or MiniGame.query.filter_by(book_id=book.id, game_type="quiz").first()
    if quiz_game is None:
        quiz_game = MiniGame(
            book_id=book.id,
            game_type="quiz",
            difficulty="easy",
            rules={},
            content={},
        )
        db.session.add(quiz_game)

    content = quiz_game.content if isinstance(quiz_game.content, dict) else {}
    config = _runtime_config(config)
    is_current_gemini = content.get("generator_version") == QUIZ_GENERATOR
    can_upgrade_fallback = content.get("generator") == "fallback" and _has_gemini_key(config)
    if is_current_gemini and not can_upgrade_fallback:
        return False

    quiz_game.content = _build_quiz_content(book, words, config)
    quiz_game.rules = {
        "questions_to_pass": max(2, min(3, len(quiz_game.content["questions"]))),
        "points_per_question": 10,
        "hint_points": 5,
    }
    return True


def create_default_mini_games(book, config=None):
    """Add each standard game once, without duplicating games already present."""
    words = _keywords(book)
    existing_types = {
        game.game_type for game in MiniGame.query.filter_by(book_id=book.id).all()
    }
    games = []
    defaults = [
        ("word_puzzle", "easy", {"time_limit_seconds": 60}, {"words": words}),
        ("spelling", "medium", {"lives": 3}, {"words": words}),
    ]
    for game_type, difficulty, rules, content in defaults:
        if game_type not in existing_types:
            game = MiniGame(
                book_id=book.id,
                game_type=game_type,
                difficulty=difficulty,
                rules=rules,
                content=content,
            )
            db.session.add(game)
            games.append(game)
    quiz = MiniGame.query.filter_by(book_id=book.id, game_type="quiz").first()
    if ensure_story_quiz(book, quiz, config):
        updated_quiz = quiz or MiniGame.query.filter_by(book_id=book.id, game_type="quiz").first()
        if updated_quiz is not None:
            games.append(updated_quiz)
    return games
