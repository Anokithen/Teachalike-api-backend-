from flask import jsonify, request

from app.extensions import db
from app.models.reading_session_model import ReadingSession
from app.models.feedback_model import Feedback, FEEDBACK_TYPES
from app.controllers.reading_session_controller import _session_belongs_to_current_parent


def _generate_feedback_text(session, feedback_type):
    """Placeholder for the real AI feedback generator.

    Swap this for a call to the AI/voice-cloning service, using the parent's
    VoiceProfile to synthesize the returned `feedback_text` as audio.
    """
    score = session.accuracy_score
    if feedback_type == "praise":
        return "Great job reading that page! Your pronunciation is really improving."
    if feedback_type == "correction":
        return "Let's try that tricky word again together, nice and slow."
    if score is not None and score < 70:
        return "You're doing well — keep sounding out the longer words one syllable at a time."
    return "Nice reading! Try reading a little slower on the long sentences next time."


def create_feedback(session_id):
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404

    data = request.get_json(silent=True) or {}
    feedback_type = data.get("feedback_type", "tip")
    if feedback_type not in FEEDBACK_TYPES:
        return jsonify({"errors": [f"feedback_type must be one of {list(FEEDBACK_TYPES)}."]}), 400

    try:
        feedback_text = _generate_feedback_text(session, feedback_type)
        feedback = Feedback(
            session_id=session.id,
            feedback_text=feedback_text,
            feedback_type=feedback_type,
            # audio_url would be populated once the cloned-voice audio is synthesized
        )
        db.session.add(feedback)
        db.session.commit()
        return jsonify(
            {"message": "Feedback generated.", "feedback": feedback.to_dict()}
        ), 201
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


def list_feedback(session_id):
    session = db.session.get(ReadingSession, session_id)
    if not _session_belongs_to_current_parent(session):
        return jsonify({"error": "Reading session not found."}), 404

    entries = (
        Feedback.query.filter_by(session_id=session_id)
        .order_by(Feedback.created_at.asc())
        .all()
    )
    return jsonify({"feedback": [f.to_dict() for f in entries]}), 200
