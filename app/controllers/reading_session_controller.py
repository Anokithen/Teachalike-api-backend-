import re
import math

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user

from app.extensions import db
from app.utils import utc_now
from app.models.child_model import Child
from app.models.book_model import Book
from app.models.voice_profile_model import VoiceProfile
from app.models.reading_session_model import ReadingSession
from app.models.pronunciation_attempt_model import PronunciationAttempt
from app.middleware import child_belongs_to_current_parent, voice_profile_belongs_to_current_parent
from app.controllers.game_result_controller import _award_leaderboard_points
from app.services.nvidia_speech_service import NvidiaSpeechError, transcribe_audio
from app.services.groq_service import GroqError, score_pronunciation as score_groq_pronunciation
from app.services.cloudinary_service import validate_uploaded_file
from app.services.pronunciation_comparison_service import compare_pronunciation
from app.security import pronunciation_requests


MAX_PRONUNCIATION_POINTS = 50


def _enforce_pronunciation_rate_limit(session_id):
    limit = current_app.config["PRONUNCIATION_RATE_LIMIT_ATTEMPTS"]
    window = current_app.config["PRONUNCIATION_RATE_LIMIT_WINDOW_SECONDS"]
    key = f"pronunciation:{current_user.id}:{session_id}"
    blocked, retry_after = pronunciation_requests.blocked(key, limit, window)
    if blocked:
        response = jsonify({"error": "That was lots of practice! Please wait a moment, then try again."})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429
    pronunciation_requests.record_failure(key, window)
    return None


def _book_paragraphs(text):
    """Return reading paragraphs made from six sentences each."""
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or "")
        if sentence.strip()
    ]
    return [
        " ".join(sentences[index : index + 6])
        for index in range(0, len(sentences), 6)
    ]


def _points_for_accuracy(score_percent):
    """Award up to 50 points, reducing one point for every two percent lost."""
    return min(MAX_PRONUNCIATION_POINTS, max(0, math.ceil(score_percent / 2)))


def _session_belongs_to_current_parent(session):
    if session is None:
        return False
    child = db.session.get(Child, session.child_id)
    return child_belongs_to_current_parent(child)


def _get_integer_id(model, value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        identifier = int(value)
    except (TypeError, ValueError):
        return None
    return db.session.get(model, identifier) if identifier > 0 else None


def create_reading_session():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required."}), 400

    errors = []
    child_id = data.get("child_id")
    book_id = data.get("book_id")
    voice_profile_id = data.get("voice_profile_id")

    child = _get_integer_id(Child, child_id)
    if not child_id or not child_belongs_to_current_parent(child):
        errors.append("A valid child_id belonging to this account is required.")
    elif child:
        child_id = child.id

    book = _get_integer_id(Book, book_id)
    if not book_id or not book:
        errors.append("A valid book_id is required.")
    elif book:
        book_id = book.id

    voice_profile = None
    if voice_profile_id is not None:
        voice_profile = _get_integer_id(VoiceProfile, voice_profile_id)
        if not voice_profile_belongs_to_current_parent(voice_profile):
            errors.append("voice_profile_id must reference a voice profile owned by this account.")
        elif voice_profile.status != "ready":
            errors.append("The selected voice profile is not ready yet.")
        else:
            voice_profile_id = voice_profile.id

    if errors:
        return jsonify({"errors": errors}), 400

    try:
        session = ReadingSession(
            child_id=child_id,
            book_id=book_id,
            voice_profile_id=voice_profile_id,
            started_at=utc_now(),
        )
        db.session.add(session)
        db.session.commit()
        return jsonify(
            {"message": "Reading session started.", "reading_session": session.to_dict()}
        ), 201
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def update_reading_session(session_id):
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required."}), 400

    try:
        if "progress_entry" in data:
            entry = data.get("progress_entry")
            if not isinstance(entry, dict):
                return jsonify({"error": "progress_entry must be an object."}), 400
            log = list(session.progress_log or [])
            log.append(entry)
            session.progress_log = log

        if "accuracy_score" in data and data.get("accuracy_score") is not None:
            accuracy_score = float(data.get("accuracy_score"))
            if not math.isfinite(accuracy_score) or not 0 <= accuracy_score <= 100:
                db.session.rollback()
                return jsonify({"error": "accuracy_score must be between 0 and 100."}), 400
            session.accuracy_score = accuracy_score

        if data.get("mark_complete"):
            session.completed_at = utc_now()

        db.session.commit()
        return jsonify(
            {"message": "Reading session updated.", "reading_session": session.to_dict()}
        ), 200
    except (TypeError, ValueError):
        db.session.rollback()
        return jsonify({"error": "accuracy_score must be a number between 0 and 100."}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def get_reading_session(session_id):
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404
    return jsonify({"reading_session": session.to_dict()}), 200


def transcribe_pronunciation(session_id):
    """Transcribe a microphone recording with NVIDIA's hosted ASR service."""
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404
    if session.completed_at:
        return jsonify({"error": "This reading session is already complete."}), 400
    limited = _enforce_pronunciation_rate_limit(session_id)
    if limited:
        return limited
    recording = request.files.get("audio")
    if recording is None or not recording.filename:
        return jsonify({"error": "A microphone recording is required."}), 400
    try:
        validate_uploaded_file(recording, "audio")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        transcript = transcribe_audio(recording)
        if not transcript:
            return jsonify({"error": "No words were heard. Try again a little closer to the microphone."}), 422
        return jsonify({"transcript": transcript}), 200
    except NvidiaSpeechError as err:
        return jsonify({"error": str(err)}), 503


def check_pronunciation(session_id):
    """Extend the existing pronunciation flow with deterministic word alignment."""
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404
    if session.completed_at:
        return jsonify({"error": "This reading session is already complete."}), 400
    limited = _enforce_pronunciation_rate_limit(session_id)
    if limited:
        return limited

    data = request.get_json(silent=True) or {}
    # Accept the old key for clients that have not been updated yet.
    paragraph_index = data.get("paragraph_index", data.get("sentence_index"))
    transcript = data.get("transcript")
    if isinstance(paragraph_index, bool) or not isinstance(paragraph_index, int) or paragraph_index < 0:
        return jsonify({"error": "A valid paragraph_index is required."}), 400
    if not isinstance(transcript, str) or not transcript.strip():
        return jsonify({"error": "A spoken transcript is required."}), 400
    if len(transcript) > 1000:
        return jsonify({"error": "The spoken transcript is too long."}), 400
    paragraphs = _book_paragraphs(session.book.text_content if session.book else None)
    if paragraph_index >= len(paragraphs):
        return jsonify({"error": "That paragraph does not exist in this book."}), 400

    original_text = paragraphs[paragraph_index]
    spoken_text = transcript.strip()
    try:
        comparison = compare_pronunciation(original_text, spoken_text, paragraph_index)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    text_match_accuracy = comparison.pop("text_match_accuracy")

    selected_model = str(current_app.config.get("GROQ_MODEL") or "").strip() or None
    scoring_provider = "groq"
    scoring_feedback = None
    try:
        score_percent, scoring_feedback = score_groq_pronunciation(
            original_text, spoken_text, current_app.config
        )
        accuracy_percent = round(score_percent)
        provider_accuracy = accuracy_percent
    except GroqError:
        # The fallback is explicitly transcript matching, not a phonetic score.
        accuracy_percent = text_match_accuracy
        scoring_provider = "local-fallback"
        selected_model = None
        provider_accuracy = None
        scoring_feedback = (
            "Sometimes background noise can change what we hear. "
            "You can try the word again."
        )
    points_for_reading = _points_for_accuracy(accuracy_percent)
    # Lock the session row in MySQL so two simultaneous retries cannot both
    # observe an unawarded paragraph and duplicate leaderboard points.
    db.session.execute(
        db.select(ReadingSession).where(ReadingSession.id == session.id).with_for_update()
    ).scalar_one()
    db.session.refresh(session, attribute_names=["progress_log"])
    log = list(session.progress_log or [])
    legacy_award_exists = any(
        entry.get("type") == "pronunciation_check"
        and entry.get("awarded_points", 0) > 0
        and entry.get("paragraph_index", entry.get("sentence_index")) == paragraph_index
        for entry in log
        if isinstance(entry, dict)
    )
    stored_award_exists = PronunciationAttempt.query.filter_by(
        reading_session_id=session.id,
        paragraph_index=paragraph_index,
    ).filter(PronunciationAttempt.points_awarded > 0).first() is not None
    already_awarded = legacy_award_exists or stored_award_exists
    points_awarded = points_for_reading if not already_awarded else 0

    previous_attempt = PronunciationAttempt.query.filter_by(
        reading_session_id=session.id,
        paragraph_index=paragraph_index,
    ).order_by(PronunciationAttempt.created_at.desc(), PronunciationAttempt.id.desc()).first()
    improvement = None
    if previous_attempt and text_match_accuracy > previous_attempt.text_match_accuracy:
        improvement = round(text_match_accuracy - previous_attempt.text_match_accuracy)

    practice_count = comparison["summary"]["words_needing_practice"]
    if practice_count == 0:
        message = "Amazing reading! Every expected word was heard."
    elif text_match_accuracy >= 75:
        message = f"Great job! Let’s practise {practice_count} word{'s' if practice_count != 1 else ''}."
    elif text_match_accuracy >= 50:
        message = f"Good effort! Let’s practise {practice_count} word{'s' if practice_count != 1 else ''}."
    else:
        message = "Nice try—let’s read it slowly together."

    try:
        summary = comparison["summary"]
        attempt = PronunciationAttempt(
            reading_session_id=session.id,
            paragraph_index=paragraph_index,
            original_text=original_text,
            spoken_transcript=spoken_text,
            provider_accuracy=provider_accuracy,
            text_match_accuracy=text_match_accuracy,
            correct_word_count=summary["correct_words"],
            substitution_count=summary["substitutions"],
            deletion_count=summary["skipped_words"],
            insertion_count=summary["extra_words"],
            comparison_data=comparison,
            scoring_provider=scoring_provider,
            scoring_model=selected_model,
            points_awarded=points_awarded,
        )
        db.session.add(attempt)
        db.session.flush()
        log.append(
            {
                "type": "pronunciation_check",
                "paragraph_index": paragraph_index,
                "transcript": spoken_text,
                "accuracy": accuracy_percent,
                "text_match_accuracy": text_match_accuracy,
                "awarded_points": points_awarded,
                "scoring_provider": scoring_provider,
                "scoring_model": selected_model,
                "attempt_id": attempt.id,
            }
        )
        session.progress_log = log
        if points_awarded:
            _award_leaderboard_points(session.child_id, points_awarded)
        db.session.commit()
        return jsonify(
            {
                "correct": accuracy_percent > 0,
                "accuracy": accuracy_percent,
                "provider_accuracy": provider_accuracy,
                "text_match_accuracy": text_match_accuracy,
                "points_awarded": points_awarded,
                "already_awarded": already_awarded,
                "scoring_provider": scoring_provider,
                "scoring_model": selected_model,
                "feedback": scoring_feedback,
                "message": message,
                "comparison": comparison,
                "attempt_id": attempt.id,
                "improvement": improvement,
            }
        ), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def list_pronunciation_attempts(session_id):
    """Return private pronunciation history newest-first for an owned session."""
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404

    paragraph_index = request.args.get("paragraph_index")
    query = PronunciationAttempt.query.filter_by(reading_session_id=session.id)
    if paragraph_index is not None:
        try:
            parsed_index = int(paragraph_index)
        except (TypeError, ValueError):
            return jsonify({"error": "paragraph_index must be a non-negative integer."}), 400
        if parsed_index < 0 or str(parsed_index) != paragraph_index.strip():
            return jsonify({"error": "paragraph_index must be a non-negative integer."}), 400
        query = query.filter_by(paragraph_index=parsed_index)

    attempts = query.order_by(
        PronunciationAttempt.created_at.desc(),
        PronunciationAttempt.id.desc(),
    ).all()
    return jsonify({"pronunciation_attempts": [attempt.to_dict() for attempt in attempts]}), 200


def list_child_reading_sessions(child_id):
    child = db.session.get(Child, child_id)
    if not child_belongs_to_current_parent(child):
        return jsonify({"error": "Child not found."}), 404

    sessions = (
        ReadingSession.query.filter_by(child_id=child_id)
        .order_by(ReadingSession.started_at.desc())
        .all()
    )
    return jsonify({"reading_sessions": [s.to_dict() for s in sessions]}), 200
