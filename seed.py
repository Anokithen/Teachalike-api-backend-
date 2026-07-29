"""Create a complete, repeatable TeachAlike demo dataset.

Run from the project root after configuring the database in ``.env``::

    python seed.py

The script is additive and idempotent. It never deletes existing records or
changes passwords for accounts that already exist. It creates demo accounts,
children, books, mini-games, reading progress, feedback, game results, and
current-week leaderboard entries.

Voice profiles use a configurable demo URL by default. They do not create a
Cloudinary asset, because a seed should not make external uploads implicitly.
To make seeded narration records available as well, provide both of these
environment variables before running the script::

    SEED_NARRATION_AUDIO_URL=https://...
    SEED_NARRATION_CLOUDINARY_PUBLIC_ID=book_narrations/seed/demo

The default passwords can be overridden with ``SEED_*_PASSWORD`` variables.
These credentials are for development only and must be changed in production.
"""
import os
from datetime import timedelta

from app import create_app
from app.controllers.game_result_controller import _current_week_start
from app.extensions import db
from app.models.book_model import Book
from app.models.book_narration_model import BookNarration, STATUS_READY as NARRATION_READY
from app.models.child_model import Child
from app.models.feedback_model import Feedback
from app.models.game_result_model import GameResult
from app.models.leaderboard_model import LeaderboardEntry
from app.models.parent_model import ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER, Parent
from app.models.reading_session_model import ReadingSession
from app.models.voice_profile_model import STATUS_READY, VoiceProfile
from app.services.book_games import create_default_mini_games
from app.utils import utc_now


DEMO_VOICE_URL = os.getenv(
    "SEED_VOICE_SAMPLE_URL",
    "https://samplelib.com/lib/preview/mp3/sample-3s.mp3",
)


BOOKS = [
    {
        "title": "The Curious Fox",
        "age_group": "4-6",
        "reading_level": "beginner",
        "cover_image_url": "https://placehold.co/600x800/f97316/ffffff?text=The+Curious+Fox",
        "text_content": (
            "Fynn the fox woke up early. He saw a bright blue butterfly near the forest path. "
            "Fynn followed it gently and found a tiny garden. He smiled and walked home before sunset."
        ),
    },
    {
        "title": "Mia's Moon Picnic",
        "age_group": "4-6",
        "reading_level": "beginner",
        "cover_image_url": "https://placehold.co/600x800/7c3aed/ffffff?text=Mia%27s+Moon+Picnic",
        "text_content": (
            "Mia packed apples, bread, and a red blanket. She sat with her puppy under the moon. "
            "They counted five shiny stars. Then Mia wished everyone a good night."
        ),
    },
    {
        "title": "The Little Seed",
        "age_group": "4-6",
        "reading_level": "beginner",
        "cover_image_url": "https://placehold.co/600x800/16a34a/ffffff?text=The+Little+Seed",
        "text_content": (
            "A little seed slept in the warm soil. Rain gave the seed a drink. "
            "The sun gave it light. Soon a yellow flower waved in the breeze."
        ),
    },
    {
        "title": "Adventures in Space",
        "age_group": "7-9",
        "reading_level": "intermediate",
        "cover_image_url": "https://placehold.co/600x800/1d4ed8/ffffff?text=Adventures+in+Space",
        "video_url": "https://res.cloudinary.com/demo/video/upload/dog.mp4",
        "text_content": (
            "Captain Mia checked every button in her small rocket ship. Beyond the window, planets glimmered like marbles. "
            "Her robot friend Atlas mapped a safe path through an asteroid field. Together they discovered a quiet purple moon."
        ),
    },
    {
        "title": "The Rainforest Rescue",
        "age_group": "7-9",
        "reading_level": "intermediate",
        "cover_image_url": "https://placehold.co/600x800/059669/ffffff?text=The+Rainforest+Rescue",
        "text_content": (
            "Nora heard a baby sloth calling from a tall rainforest tree. She and her guide built a gentle rope bridge. "
            "The sloth crossed safely to its mother. The whole forest seemed to cheer."
        ),
    },
    {
        "title": "The Inventor's Surprise",
        "age_group": "10-12",
        "reading_level": "advanced",
        "cover_image_url": "https://placehold.co/600x800/0f766e/ffffff?text=The+Inventor%27s+Surprise",
        "text_content": (
            "Arun carefully adjusted the gears inside his recycling machine. Instead of sorting paper, it began composing cheerful music. "
            "After testing each connection, Arun discovered that a loose copper wire had changed the program. "
            "He presented the musical invention at the school fair."
        ),
    },
]


def get_or_create_account(name, email, role, password):
    account = Parent.query.filter_by(email=email).first()
    if account:
        return account, False
    account = Parent(name=name, email=email, role=role)
    account.set_password(password)
    db.session.add(account)
    return account, True


def get_or_create_child(parent, teacher, name, age, gender, level, pin):
    child = Child.query.filter_by(parent_id=parent.id, name=name).first()
    if child:
        return child, False
    child = Child(
        parent_id=parent.id,
        created_by_id=teacher.id if teacher else parent.id,
        name=name,
        age=age,
        gender=gender,
        reading_level=level,
    )
    child.set_pin(pin)
    db.session.add(child)
    return child, True
