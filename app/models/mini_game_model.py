import hashlib
import random

from app.extensions import db
from app.utils import utc_isoformat, utc_now


class MiniGame(db.Model):
    __tablename__ = "mini_games"
    __table_args__ = (
        db.UniqueConstraint(
            "book_id", "game_type", "content_version",
            name="uq_mini_games_book_type_version",
        ),
        db.Index("ix_mini_games_generation_status", "generation_status"),
        db.Index("ix_mini_games_source_content_hash", "source_content_hash"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False, index=True)
    game_type = db.Column(db.String(50), nullable=False)  # e.g. word_puzzle, spelling
    difficulty = db.Column(db.String(20), nullable=False, default="easy")
    rules = db.Column(db.JSON, nullable=True)
    content = db.Column(db.JSON, nullable=True)
    generation_status = db.Column(db.String(20), nullable=False, default="pending")
    generator_provider = db.Column(db.String(50), nullable=True)
    generator_model = db.Column(db.String(200), nullable=True)
    generator_version = db.Column(db.String(50), nullable=True)
    source_content_hash = db.Column(db.String(64), nullable=True)
    generated_at = db.Column(db.DateTime, nullable=True)
    generation_error = db.Column(db.String(500), nullable=True)
    content_version = db.Column(db.Integer, nullable=True)
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
            "generation_status": self.generation_status,
            "content_version": self.content_version,
            "generated_at": utc_isoformat(self.generated_at),
            "created_at": utc_isoformat(self.created_at),
        }
        if include_content:
            data["rules"] = self.rules
            data["content"] = self.content
        return data

    @staticmethod
    def _scramble(word, seed):
        letters = list(word)
        if len(letters) < 2:
            return letters
        randomizer = random.Random(hashlib.sha256(seed.encode("utf-8")).digest())
        for _attempt in range(8):
            randomizer.shuffle(letters)
            if "".join(letters).casefold() != word.casefold():
                break
        return letters

    def to_public_dict(self):
        """Serialize playable content without hidden quiz/word-puzzle answers."""
        data = self.to_dict()
        if self.generation_status in {"pending", "generating", "failed", "stale"}:
            data["content"] = {}
            if self.book is not None:
                data["book"] = {
                    "id": self.book.id,
                    "title": self.book.title,
                    "cover_image_url": self.book.cover_image_url,
                }
            return data
        content = self.content if isinstance(self.content, dict) else {}
        if self.game_type == "quiz":
            data["content"] = {"questions": [{
                "id": str(question.get("id") or f"q_{index + 1:02d}"),
                "type": "multiple_choice",
                "question": str(question.get("question") or ""),
                "options": list(question.get("options") or []),
                "hint": str(question.get("hint") or ""),
                "difficulty": str(question.get("difficulty") or self.difficulty),
                "skill": str(question.get("skill") or "story_comprehension"),
            } for index, question in enumerate(content.get("questions") or [])
                if isinstance(question, dict)]}
        elif self.game_type in {"word_puzzle", "spelling"}:
            public_words = []
            for index, raw_word in enumerate(content.get("words") or []):
                item = raw_word if isinstance(raw_word, dict) else {"word": raw_word}
                word = str(item.get("word") or "").strip()
                if not word:
                    continue
                word_id = str(item.get("id") or f"w_{index + 1:02d}")
                public_item = {
                    "id": word_id,
                    "difficulty": str(item.get("difficulty") or self.difficulty),
                    "hint": str(item.get("hint") or "A useful word from this book."),
                }
                if self.game_type == "spelling":
                    # The current spelling activity intentionally shows the word
                    # during its memorise stage, before hiding it for recall.
                    public_item["word"] = word
                else:
                    public_item["scrambled_letters"] = self._scramble(
                        word, f"{self.id}:{self.content_version}:{word_id}"
                    )
                public_words.append(public_item)
            data["content"] = {"words": public_words}
        else:
            data["content"] = {}
        if self.book is not None:
            data["book"] = {
                "id": self.book.id,
                "title": self.book.title,
                "cover_image_url": self.book.cover_image_url,
            }
        return data
