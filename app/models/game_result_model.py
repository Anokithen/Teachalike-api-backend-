from app.extensions import db
from app.utils import utc_isoformat, utc_now


class GameResult(db.Model):
    __tablename__ = "game_results"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey("mini_games.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False, default=0)
    completed_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "child_id": self.child_id,
            "game_id": self.game_id,
            "score": self.score,
            "completed_at": utc_isoformat(self.completed_at),
        }
