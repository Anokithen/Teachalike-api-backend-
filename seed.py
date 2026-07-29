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


def get_or_create_book(data):
    book = Book.query.filter_by(title=data["title"]).first()
    if book:
        return book, False
    book = Book(**data)
    db.session.add(book)
    return book, True


def get_or_create_voice_profile(parent, label):
    """Create a playable demo profile without uploading to Cloudinary."""
    profile = VoiceProfile.query.filter_by(parent_id=parent.id, label=label).first()
    if profile:
        # Repair the old seed's example.com placeholder if this script is run
        # against a database seeded by an earlier version.
        if (profile.voice_sample_url or "").startswith("https://example.com/"):
            profile.voice_sample_url = DEMO_VOICE_URL
            profile.cloudinary_public_id = None
            profile.status = STATUS_READY
        return profile, False

    profile = VoiceProfile(
        parent_id=parent.id,
        label=label,
        voice_sample_url=DEMO_VOICE_URL,
        # A null public ID makes the API use the stored fallback URL. A real
        # uploaded profile will have a Cloudinary public ID instead.
        cloudinary_public_id=None,
        status=STATUS_READY,
    )
    db.session.add(profile)
    return profile, True


def get_or_create_session(child, book, voice_profile, *, completed, accuracy, minutes_ago):
    session = (
        ReadingSession.query.filter_by(child_id=child.id, book_id=book.id)
        .order_by(ReadingSession.id.asc())
        .first()
    )
    if session:
        return session, False

    started_at = utc_now() - timedelta(minutes=minutes_ago)
    completed_at = started_at + timedelta(minutes=8) if completed else None
    session = ReadingSession(
        child_id=child.id,
        book_id=book.id,
        voice_profile_id=voice_profile.id if voice_profile else None,
        started_at=started_at,
        completed_at=completed_at,
        accuracy_score=accuracy,
        progress_log=[
            {"type": "pronunciation_check", "sentence_index": 0, "accuracy": accuracy, "awarded_points": 10 if completed else 0},
            {"type": "pronunciation_check", "sentence_index": 1, "accuracy": max(0, accuracy - 3), "awarded_points": 10 if completed else 0},
        ],
    )
    db.session.add(session)
    return session, True


def ensure_feedback(session, feedback_type, text):
    if Feedback.query.filter_by(session_id=session.id, feedback_type=feedback_type).first():
        return False
    db.session.add(Feedback(
        session_id=session.id,
        feedback_type=feedback_type,
        feedback_text=text,
    ))
    return True


def ensure_game_result(child, game, score):
    if GameResult.query.filter_by(child_id=child.id, game_id=game.id).first():
        return False
    db.session.add(GameResult(child_id=child.id, game_id=game.id, score=score))
    return True


def ensure_leaderboard_entry(child, points, streak_count):
    entry = LeaderboardEntry.query.filter_by(
        child_id=child.id,
        week_start=_current_week_start(),
    ).first()
    if entry:
        return False
    db.session.add(LeaderboardEntry(
        child_id=child.id,
        week_start=_current_week_start(),
        points=points,
        streak_count=streak_count,
    ))
    return True


def ensure_optional_narration(book, voice_profile):
    """Seed a ready narration only when the caller provides a real asset."""
    audio_url = os.getenv("SEED_NARRATION_AUDIO_URL", "").strip()
    public_id = os.getenv("SEED_NARRATION_CLOUDINARY_PUBLIC_ID", "").strip()
    if not audio_url or not public_id:
        return False
    if BookNarration.query.filter_by(book_id=book.id, voice_profile_id=voice_profile.id).first():
        return False
    db.session.add(BookNarration(
        book_id=book.id,
        voice_profile_id=voice_profile.id,
        status=NARRATION_READY,
        narration_audio_url=audio_url,
        cloudinary_public_id=public_id,
    ))
    return True


def main():
    app = create_app()
    with app.app_context():
        db.create_all()

        account_specs = [
            ("Site Admin", os.getenv("SEED_ADMIN_EMAIL", "admin@teachalike.app"), ROLE_ADMIN, os.getenv("SEED_ADMIN_PASSWORD", "ChangeMe123!")),
            ("Jamie Perera", os.getenv("SEED_PARENT_EMAIL", "jamie@teachalike.app"), ROLE_PARENT, os.getenv("SEED_PARENT_PASSWORD", "ParentDemo123!")),
            ("Priya Fernando", os.getenv("SEED_PARENT_2_EMAIL", "priya@teachalike.app"), ROLE_PARENT, os.getenv("SEED_PARENT_2_PASSWORD", "ParentDemo123!")),
            ("Alex Silva", os.getenv("SEED_TEACHER_EMAIL", "alex.teacher@teachalike.app"), ROLE_TEACHER, os.getenv("SEED_TEACHER_PASSWORD", "TeacherDemo123!")),
        ]
        accounts = {}
        accounts_created = 0
        for key, name, email, role, password in (
            ("admin", *account_specs[0]),
            ("parent", *account_specs[1]),
            ("parent_2", *account_specs[2]),
            ("teacher", *account_specs[3]),
        ):
            accounts[key], created = get_or_create_account(name, email, role, password)
            accounts_created += int(created)
        db.session.flush()

        child_specs = [
            (accounts["parent"], None, "Ava", 6, "female", "beginner", "123456"),
            (accounts["parent"], accounts["teacher"], "Noah", 8, "male", "intermediate", "234567"),
            (accounts["parent_2"], accounts["teacher"], "Maya", 10, "female", "advanced", "345678"),
            (accounts["parent_2"], None, "Leo", 5, "male", "beginner", "456789"),
        ]
        children = []
        children_created = 0
        for spec in child_specs:
            child, created = get_or_create_child(*spec)
            children.append(child)
            children_created += int(created)
        db.session.flush()

        books = []
        books_created = 0
        for data in BOOKS:
            book, created = get_or_create_book(data)
            books.append(book)
            books_created += int(created)
        db.session.flush()

        games_added = 0
        for book in books:
            games_added += len(create_default_mini_games(book))
        db.session.flush()

        jamie_voice, jamie_voice_created = get_or_create_voice_profile(accounts["parent"], "Jamie's voice")
        priya_voice, priya_voice_created = get_or_create_voice_profile(accounts["parent_2"], "Priya's voice")
        db.session.flush()

        voice_by_child = {
            children[0].id: jamie_voice,
            children[1].id: jamie_voice,
            children[2].id: priya_voice,
            children[3].id: priya_voice,
        }
        sessions_created = 0
        feedback_created = 0
        game_results_created = 0
        leaderboard_created = 0

        quiz_games = {
            book.id: next((game for game in book.mini_games if game.game_type == "quiz"), None)
            for book in books
        }
        for child_index, child in enumerate(children):
            voice_profile = voice_by_child[child.id]
            completed, created = get_or_create_session(
                child,
                books[child_index % 3],
                voice_profile,
                completed=True,
                accuracy=92 - child_index * 4,
                minutes_ago=1440 + child_index * 30,
            )
            sessions_created += int(created)
            db.session.flush()
            feedback_created += int(ensure_feedback(
                completed,
                "praise",
                "Wonderful reading! You used a clear, confident voice.",
            ))
            feedback_created += int(ensure_feedback(
                completed,
                "tip",
                "Try pausing briefly at each full stop before you continue.",
            ))

            _, created = get_or_create_session(
                child,
                books[(child_index % 3) + 3],
                voice_profile,
                completed=False,
                accuracy=84 - child_index * 3,
                minutes_ago=3 + child_index,
            )
            sessions_created += int(created)

            for book_index, score in ((child_index % 3, 30 + child_index * 4), ((child_index + 1) % 3, 42 + child_index * 3)):
                game = quiz_games[books[book_index].id]
                if game:
                    game_results_created += int(ensure_game_result(child, game, score))

            leaderboard_created += int(ensure_leaderboard_entry(
                child,
                points=70 + child_index * 15,
                streak_count=3 + child_index,
            ))

        db.session.flush()
        narrations_created = int(ensure_optional_narration(books[0], jamie_voice))
        db.session.commit()

        print("TeachAlike demo data is ready.")
        print(
            "Created: "
            f"{accounts_created} accounts, {children_created} children, "
            f"{books_created} books, {games_added} mini-games, "
            f"{int(jamie_voice_created) + int(priya_voice_created)} voice profiles, "
            f"{sessions_created} reading sessions, {feedback_created} feedback entries, "
            f"{game_results_created} game results, {leaderboard_created} leaderboard entries, "
            f"{narrations_created} narration records."
        )
        print("Demo logins:")
        print(f"  Admin:   {accounts['admin'].email} / {account_specs[0][3]}")
        print(f"  Parent:  {accounts['parent'].email} / {account_specs[1][3]}")
        print(f"  Parent 2:{accounts['parent_2'].email} / {account_specs[2][3]}")
        print(f"  Teacher: {accounts['teacher'].email} / {account_specs[3][3]}")
        print("Child PINs: Ava 123456, Noah 234567, Maya 345678, Leo 456789")
        if not os.getenv("SEED_NARRATION_AUDIO_URL") or not os.getenv("SEED_NARRATION_CLOUDINARY_PUBLIC_ID"):
            print("Narration asset skipped; set SEED_NARRATION_AUDIO_URL and SEED_NARRATION_CLOUDINARY_PUBLIC_ID to seed one.")


if __name__ == "__main__":
    main()
