from app.extensions import db
from app.utils import utc_isoformat, utc_now


class MiniGame(db.Model):
    __tablename__ = "mini_games"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    game_type = db.Column(db.String(50), nullable=False)  # e.g. word_puzzle, spelling
    difficulty = db.Column(db.String(20), nullable=False, default="easy")
    rules = db.Column(db.JSON, nullable=True)
    content = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    results = db.relationship(
        "GameResult", backref="game", cascade="all, delete-orphan", lazy=True
    )

    def to_dict(self, include_content=False):
        data = {
            "id": self.id,
            "book_id": self.book_id,
            "game_type": self.game_type,
            "difficulty": self.difficulty,
            "created_at": utc_isoformat(self.created_at),
        }
        if include_content:
            data["rules"] = self.rules
            data["content"] = self.content
        return data
