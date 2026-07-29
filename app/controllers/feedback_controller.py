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
