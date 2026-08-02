"""Versioned, book-grounded generation for the three built-in mini-games."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from flask import current_app

from app.extensions import db
from app.models.book_model import Book
from app.models.mini_game_model import MiniGame
from app.services.gemini_service import GeminiError, generate_book_game_bundle
from app.utils import utc_now


GAME_TYPES = ("word_puzzle", "spelling", "quiz")
GENERATION_STATUSES = ("pending", "generating", "ready", "fallback", "failed", "stale")
GENERATOR_VERSION = "book-games-v3"
MAX_GENERATION_BOOK_CHARACTERS = 120_000
MIN_GROUNDED_WORDS = 5
DIFFICULTIES = {"easy", "medium", "hard"}
SKILLS = {
    "story_comprehension", "character", "event", "sequence",
    "vocabulary", "main_idea",
}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "he", "her", "his", "i", "in", "is", "it", "of", "on", "or", "she",
    "the", "their", "they", "this", "that", "to", "was", "were", "with",
    "you", "your", "என்று", "ஒரு", "இந்த", "அது", "மற்றும்", "இல்",
}
UNSAFE_OUTPUT_RE = re.compile(
    r"<\s*/?\s*[a-z][^>]*>|system\s+prompt|x-goog-api-key|gemini_api_key|provider\s+headers?",
    re.IGNORECASE,
)


class GameGenerationValidationError(ValueError):
    """Raised when provider content is unsafe, malformed, or ungrounded."""


def _runtime_config(config=None):
    if config is not None:
        return config
    try:
        return current_app.config
    except RuntimeError:
        return os.environ


def _normalise(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n.,!?;:'\"“”‘’()[]{}-")


def _is_word_character(character):
    return bool(character) and unicodedata.category(character)[0] in {"L", "M", "N"}


def _word_occurrences(text):
    occurrences = []
    start = None
    for index, character in enumerate(text):
        if _is_word_character(character):
            if start is None:
                start = index
            continue
        if character in {"'", "’"} and start is not None and index + 1 < len(text) and _is_word_character(text[index + 1]):
            continue
        if start is not None:
            occurrences.append((text[start:index], start, index))
            start = None
    if start is not None:
        occurrences.append((text[start:], start, len(text)))
    return occurrences


def _sentence_for_span(text, start, end, max_length=240):
    left = max(text.rfind(".", 0, start), text.rfind("!", 0, start), text.rfind("?", 0, start), text.rfind("\n", 0, start))
    right_candidates = [position for marker in ".!?\n" if (position := text.find(marker, end)) >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    raw_excerpt = text[left + 1:right]
    if len(raw_excerpt) > max_length:
        window_start = max(left + 1, start - max_length // 3)
        raw_excerpt = text[window_start:window_start + max_length]
    return " ".join(raw_excerpt.split())


def _book_snapshot(book):
    text = str(book.text_content or "").strip()
    return {
        "id": book.id,
        "title": str(book.title or "").strip(),
        "text_content": text,
        "age_group": str(book.age_group or "").strip(),
        "reading_level": str(book.reading_level or "beginner").strip().lower(),
    }


def source_content_hash(book_or_snapshot):
    snapshot = book_or_snapshot if isinstance(book_or_snapshot, dict) else _book_snapshot(book_or_snapshot)
    source = json.dumps({
        "title": snapshot.get("title") or "",
        "text_content": snapshot.get("text_content") or "",
        "age_group": snapshot.get("age_group") or "",
        "reading_level": snapshot.get("reading_level") or "",
        "generator_version": GENERATOR_VERSION,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(unicodedata.normalize("NFKC", source).encode("utf-8")).hexdigest()


def detect_book_language(text):
    letters = [character for character in text if unicodedata.category(character).startswith("L")]
    if not letters:
        return "English"
    tamil = sum("\u0b80" <= character <= "\u0bff" for character in letters)
    sinhala = sum("\u0d80" <= character <= "\u0dff" for character in letters)
    if tamil / len(letters) >= 0.2:
        return "Tamil"
    if sinhala / len(letters) >= 0.2:
        return "Sinhala"
    return "English"


def question_count_for_text(text):
    count = len(_word_occurrences(text))
    return 5 if count < 100 else 8 if count < 500 else 10


def _safe_string(value, maximum):
    if not isinstance(value, str):
        raise GameGenerationValidationError("Generated content contains a non-string value.")
    value = " ".join(value.split()).strip()
    if not value or len(value) > maximum or UNSAFE_OUTPUT_RE.search(value):
        raise GameGenerationValidationError("Generated content contains an unsafe or invalid value.")
    return value


def _excerpt_is_grounded(excerpt, text):
    return bool(_normalise(excerpt)) and _normalise(excerpt) in _normalise(text)


def _validate_questions(raw_questions, snapshot, requested_count):
    if not isinstance(raw_questions, list) or len(raw_questions) != requested_count:
        raise GameGenerationValidationError("The generated quiz has the wrong number of questions.")
    text = snapshot["text_content"]
    normalised_text = _normalise(text)
    questions = []
    normalised_questions = []
    for index, raw in enumerate(raw_questions):
        if not isinstance(raw, dict):
            raise GameGenerationValidationError("A generated question is not an object.")
        question_text = _safe_string(raw.get("question"), 280)
        question_key = _normalise(question_text)
        if any(
            question_key == previous or SequenceMatcher(None, question_key, previous).ratio() >= 0.9
            for previous in normalised_questions
        ):
            raise GameGenerationValidationError("The generated quiz contains duplicate questions.")
        options = raw.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise GameGenerationValidationError("Each generated question must have four options.")
        options = [_safe_string(option, 100) for option in options]
        if len({_normalise(option) for option in options}) != 4:
            raise GameGenerationValidationError("Generated question options must be unique.")
        correct_index = raw.get("correct_option_index")
        if isinstance(correct_index, bool) or not isinstance(correct_index, int) or not 0 <= correct_index <= 3:
            raise GameGenerationValidationError("A generated correct answer index is invalid.")
        excerpt = _safe_string(raw.get("source_excerpt"), 320)
        if not _excerpt_is_grounded(excerpt, text):
            raise GameGenerationValidationError("A generated source excerpt is not in the book.")
        correct_answer = _normalise(options[correct_index])
        if correct_answer not in _normalise(excerpt) and correct_answer not in normalised_text:
            raise GameGenerationValidationError("A generated answer is not supported by the book.")
        difficulty = _safe_string(raw.get("difficulty"), 20).lower()
        skill = _safe_string(raw.get("skill"), 40).lower()
        if difficulty not in DIFFICULTIES or skill not in SKILLS:
            raise GameGenerationValidationError("A generated category is unsupported.")
        questions.append({
            "id": f"q_{index + 1:02d}",
            "type": "multiple_choice",
            "question": question_text,
            "options": options,
            "correct_option_index": correct_index,
            "hint": _safe_string(raw.get("hint"), 200),
            "explanation": _safe_string(raw.get("explanation"), 280),
            "source_excerpt": excerpt,
            "difficulty": difficulty,
            "skill": skill,
        })
        normalised_questions.append(question_key)
    return questions


def _difficulty_for_position(index, total):
    if total <= 2:
        return "easy" if index == 0 else "medium"
    if index < max(1, round(total * 0.3)):
        return "easy"
    if index < max(2, round(total * 0.6)):
        return "medium"
    return "hard"


def _book_word_map(text):
    result = {}
    for word, start, end in _word_occurrences(text):
        key = _normalise(word)
        if key and key not in result:
            result[key] = (word, start, end)
    return result


def _validate_words(raw_words, snapshot, *, prefix, minimum=5, maximum=10):
    if not isinstance(raw_words, list):
        raise GameGenerationValidationError("Generated word content must be a list.")
    text = snapshot["text_content"]
    word_map = _book_word_map(text)
    words = []
    seen = set()
    for raw in raw_words:
        if not isinstance(raw, dict):
            raise GameGenerationValidationError("A generated word is not an object.")
        requested_word = _safe_string(raw.get("word"), 80)
        key = _normalise(requested_word)
        if key in seen:
            continue
        if key in STOP_WORDS or key not in word_map or len(key) < 3:
            raise GameGenerationValidationError(
                "A generated word is not a suitable word from the saved book."
            )
        excerpt = _safe_string(raw.get("source_excerpt"), 320)
        if not _excerpt_is_grounded(excerpt, text):
            raise GameGenerationValidationError("A generated word excerpt is not in the book.")
        difficulty = _safe_string(raw.get("difficulty"), 20).lower()
        if difficulty not in DIFFICULTIES:
            raise GameGenerationValidationError("A generated word difficulty is invalid.")
        original_word, _start, _end = word_map[key]
        words.append({
            "id": f"{prefix}_{len(words) + 1:02d}",
            "word": original_word,
            "difficulty": difficulty,
            "source_excerpt": excerpt,
            "hint": _safe_string(raw.get("hint"), 180),
        })
        seen.add(key)
        if len(words) == maximum:
            break
    if len(words) < minimum:
        raise GameGenerationValidationError("Not enough grounded words were generated.")
    return words


def validate_generated_bundle(raw_bundle, snapshot, requested_count):
    if not isinstance(raw_bundle, dict):
        raise GameGenerationValidationError("Generated game content is not an object.")
    return {
        "quiz": _validate_questions(raw_bundle.get("questions"), snapshot, requested_count),
        "word_puzzle": _validate_words(
            raw_bundle.get("word_puzzle_words"), snapshot, prefix="p"
        ),
        "spelling": _validate_words(
            raw_bundle.get("spelling_words"), snapshot, prefix="s"
        ),
    }


def _ranked_story_words(snapshot, maximum=10):
    text = snapshot["text_content"]
    occurrences = _word_occurrences(text)
    counts = Counter(_normalise(word) for word, _start, _end in occurrences)
    first = {}
    for word, start, end in occurrences:
        key = _normalise(word)
        if key not in first:
            first[key] = (word, start, end)
    candidates = [
        (key, *value) for key, value in first.items()
        if len(key) >= 3 and key not in STOP_WORDS
    ]
    candidates.sort(key=lambda item: (-counts[item[0]], -len(item[0]), item[1]))
    selected = sorted(candidates[:maximum], key=lambda item: item[2])
    return selected


def deterministic_fallback_bundle(snapshot):
    selected = _ranked_story_words(snapshot, maximum=10)
    if len(selected) < MIN_GROUNDED_WORDS:
        raise GameGenerationValidationError("This book needs more story words for mini-games.")
    text = snapshot["text_content"]
    language = detect_book_language(text)
    word_items = []
    for index, (_key, word, start, end) in enumerate(selected):
        excerpt = _sentence_for_span(text, start, end)
        difficulty = _difficulty_for_position(index, len(selected))
        hint = (
            "இந்த வார்த்தை கதையில் தோன்றுகிறது."
            if language == "Tamil"
            else "This useful word appears in the story."
        )
        word_items.append({
            "word": word,
            "difficulty": difficulty,
            "source_excerpt": excerpt,
            "hint": hint,
        })

    requested_count = question_count_for_text(text)
    quiz_words = word_items[:requested_count]
    questions = []
    all_options = [item["word"] for item in word_items]
    skills = ["vocabulary", "story_comprehension", "event", "sequence", "main_idea"]
    for index, item in enumerate(quiz_words):
        word = item["word"]
        excerpt = item["source_excerpt"]
        masked = re.sub(re.escape(word), "____", excerpt, count=1, flags=re.IGNORECASE)
        options = [word]
        for offset in range(1, len(all_options) + 1):
            candidate = all_options[(index + offset) % len(all_options)]
            if _normalise(candidate) not in {_normalise(option) for option in options}:
                options.append(candidate)
            if len(options) == 4:
                break
        rotation = index % 4
        options = options[rotation:] + options[:rotation]
        questions.append({
            "id": f"q_{index + 1:02d}",
            "type": "multiple_choice",
            "question": (
                f"கதையின் இந்த இடைவெளியை எந்த வார்த்தை நிறைவு செய்கிறது? “{masked}”"
                if language == "Tamil"
                else f"Which word completes this line from the story? “{masked}”"
            )[:280],
            "options": options,
            "correct_option_index": options.index(word),
            "hint": (
                "கதையில் அந்த வாக்கியத்தை நினைவுபடுத்துங்கள்."
                if language == "Tamil"
                else "Think about the exact words used in that part of the story."
            ),
            "explanation": (
                f"“{word}” என்பது கதையில் பயன்படுத்தப்பட்ட வார்த்தை."
                if language == "Tamil"
                else f"“{word}” is the word used in the saved story."
            ),
            "source_excerpt": excerpt,
            "difficulty": item["difficulty"],
            "skill": skills[index % len(skills)],
        })

    puzzle_words = [{"id": f"p_{index + 1:02d}", **item} for index, item in enumerate(word_items[:8])]
    spelling_words = [{"id": f"s_{index + 1:02d}", **item} for index, item in enumerate(word_items)]
    return {"quiz": questions, "word_puzzle": puzzle_words, "spelling": spelling_words}


def _rules_for(game_type, content):
    if game_type == "quiz":
        return {
            "points_per_question": 10,
            "hint_points": 5,
            "questions_to_pass": max(2, min(5, len(content))),
        }
    if game_type == "word_puzzle":
        return {"points_per_word": 10, "time_limit_seconds": 60}
    return {"points_per_word": 10, "lives": 3, "difficulty_groups": ["easy", "medium", "hard"]}


def _new_game(book_id, game_type, fingerprint, version):
    difficulty = "medium" if game_type == "spelling" else "easy"
    return MiniGame(
        book_id=book_id,
        game_type=game_type,
        difficulty=difficulty,
        rules={},
        content={},
        generation_status="pending",
        generator_version=GENERATOR_VERSION,
        source_content_hash=fingerprint,
        content_version=version,
    )


def create_default_mini_games(book, config=None):
    """Create pending standard records once; external generation happens later."""
    fingerprint = source_content_hash(book)
    existing_types = {
        game.game_type for game in MiniGame.query.filter(
            MiniGame.book_id == book.id,
            MiniGame.generation_status != "stale",
        ).all()
    }
    current_version = max(
        [game.content_version or 0 for game in MiniGame.query.filter_by(book_id=book.id).all()] or [0]
    ) or 1
    created = []
    for game_type in GAME_TYPES:
        if game_type not in existing_types:
            game = _new_game(book.id, game_type, fingerprint, current_version)
            db.session.add(game)
            created.append(game)
    return created


def active_book_games(book_id):
    games = MiniGame.query.filter(
        MiniGame.book_id == book_id,
        MiniGame.generation_status != "stale",
    ).order_by(MiniGame.id.asc()).all()
    newest = {}
    for game in games:
        current = newest.get(game.game_type)
        if current is None or (game.content_version or 0, game.id) > (current.content_version or 0, current.id):
            newest[game.game_type] = game
    return [newest[game_type] for game_type in GAME_TYPES if game_type in newest]


def ensure_book_games(book_id, *, force=False, config=None):
    """Create/reuse a version and generate it without holding a DB transaction."""
    config = _runtime_config(config)
    book = db.session.execute(
        db.select(Book).where(Book.id == book_id).with_for_update()
    ).scalar_one_or_none()
    if book is None:
        return [], False
    snapshot = _book_snapshot(book)
    fingerprint = source_content_hash(snapshot)
    active = active_book_games(book.id)
    by_type = {game.game_type: game for game in active}
    matches = (
        set(by_type) == set(GAME_TYPES)
        and len({game.content_version for game in active}) == 1
        and all(game.source_content_hash == fingerprint for game in active)
    )
    if matches and not force:
        statuses = {game.generation_status for game in active}
        if "generating" in statuses or not statuses.intersection({"pending"}):
            db.session.commit()
            return active, False
        games = active
    else:
        for game in active:
            game.generation_status = "stale"
        maximum_version = db.session.query(db.func.max(MiniGame.content_version)).filter_by(
            book_id=book.id
        ).scalar() or 0
        version = maximum_version + 1
        games = [_new_game(book.id, game_type, fingerprint, version) for game_type in GAME_TYPES]
        db.session.add_all(games)
        db.session.flush()

    for game in games:
        game.generation_status = "generating"
        game.generation_error = None
    game_ids = [game.id for game in games]
    db.session.commit()

    text = snapshot["text_content"]
    provider = "fallback"
    model = None
    safe_error = None
    try:
        fallback = deterministic_fallback_bundle(snapshot)
    except GameGenerationValidationError:
        fallback = None

    bundle = None
    has_key = bool(str(config.get("GEMINI_API_KEY") or config.get("GOOGLE_API_KEY") or "").strip())
    if fallback is not None and has_key:
        retries = max(1, min(3, int(config.get("MINI_GAME_GENERATION_RETRIES", 2))))
        requested_count = question_count_for_text(text)
        for _attempt in range(retries):
            try:
                raw = generate_book_game_bundle(
                    snapshot, config, requested_count, detect_book_language(text)
                )
                bundle = validate_generated_bundle(raw, snapshot, requested_count)
                provider = "gemini"
                model = str(config.get("GEMINI_MODEL") or "").strip() or None
                break
            # Provider SDKs may raise transport/library-specific exceptions.
            # Treat every provider-call failure as retryable here and never let
            # it abort book creation or leak provider details to a client.
            except Exception:
                continue
    if bundle is None:
        bundle = fallback
        safe_error = (
            "AI generation was unavailable, so a book-based fallback was prepared."
            if fallback is not None
            else "This book needs a little more text before games can be prepared."
        )

    current_book = db.session.get(Book, book_id)
    stored_games = [db.session.get(MiniGame, game_id) for game_id in game_ids]
    if current_book is None or source_content_hash(current_book) != fingerprint:
        for game in stored_games:
            if game is not None:
                game.generation_status = "stale"
        db.session.commit()
        return [game for game in stored_games if game is not None], True

    generated_at = utc_now()
    for game in stored_games:
        if game is None:
            continue
        game.generator_provider = provider if bundle is not None else None
        game.generator_model = model
        game.generator_version = GENERATOR_VERSION
        game.generated_at = generated_at if bundle is not None else None
        game.generation_error = safe_error
        if bundle is None:
            game.generation_status = "failed"
            game.content = {}
            game.rules = {}
            continue
        generated_content = bundle[game.game_type]
        key = "questions" if game.game_type == "quiz" else "words"
        game.content = {key: generated_content}
        game.rules = _rules_for(game.game_type, generated_content)
        game.generation_status = "ready" if provider == "gemini" else "fallback"
    db.session.commit()
    return [game for game in stored_games if game is not None], True


def mark_book_games_stale(book_id):
    """Mark only active generated content stale; historical rows/results remain."""
    changed = False
    for game in active_book_games(book_id):
        if game.generation_status != "stale":
            game.generation_status = "stale"
            changed = True
    return changed
