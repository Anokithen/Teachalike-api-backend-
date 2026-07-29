import re
import math
from difflib import SequenceMatcher

from flask import current_app, jsonify, request
from flask_jwt_extended import current_user

from app.extensions import db
from app.utils import utc_now
from app.models.child_model import Child
from app.models.book_model import Book
from app.models.voice_profile_model import VoiceProfile
from app.models.reading_session_model import ReadingSession
from app.middleware import child_belongs_to_current_parent, voice_profile_belongs_to_current_parent
from app.controllers.game_result_controller import _award_leaderboard_points
from app.services.nvidia_speech_service import NvidiaSpeechError, transcribe_audio
from app.services.groq_service import GroqError, score_pronunciation as score_groq_pronunciation
from app.services.cloudinary_service import validate_uploaded_file


MAX_PRONUNCIATION_POINTS = 50


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


def _normalise_spoken_text(text):
    return " ".join(re.findall(r"[\w']+", text.lower()))


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
