from app.extensions import db
from app.utils import utc_isoformat, utc_now


class GameResult(db.Model):
    __tablename__ = "game_results"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False, index=True)
    game_id = db.Column(db.Integer, db.ForeignKey("mini_games.id"), nullable=False, index=True)
    score = db.Column(db.Integer, nullable=False, default=0)
    correct_answers = db.Column(db.Integer, nullable=True)
    total_questions = db.Column(db.Integer, nullable=True)
    answers_data = db.Column(db.JSON, nullable=True)
    game_content_version = db.Column(db.Integer, nullable=True)
    points_awarded = db.Column(db.Integer, nullable=False, default=0)
    completed_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "child_id": self.child_id,
            "game_id": self.game_id,
            "score": self.score,
            "correct_answers": self.correct_answers,
            "total_questions": self.total_questions,
            "game_content_version": self.game_content_version,
            "points_awarded": self.points_awarded,
            "completed_at": utc_isoformat(self.completed_at),
        }
