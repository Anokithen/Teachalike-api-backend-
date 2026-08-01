"""Create repeatable local demo data without external assets.

Run from the API project root after configuring the database::

    python seed.py

The seed is additive and idempotent: it never deletes existing data or resets
passwords for existing accounts. It intentionally creates no voice profiles,
book narrations, Cloudinary ``Asset`` rows, profile images, book images, or
videos. Reading sessions therefore use the application's normal default voice.

The demo passwords can be overridden with ``SEED_*_PASSWORD`` environment
variables. They are development credentials and must not be used in production.
"""

import os
from datetime import timedelta

from app import create_app
from app.extensions import db
from app.models.book_model import Book
from app.models.child_model import Child
from app.models.feedback_model import Feedback
from app.models.game_result_model import GameResult
from app.models.leaderboard_model import LeaderboardEntry
from app.models.mini_game_model import MiniGame
from app.models.parent_model import ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER, Parent
from app.models.reading_session_model import ReadingSession
from app.services.book_games import create_default_mini_games
from app.utils import utc_now


BOOKS = (
    {
        "title": "The Curious Fox",
        "age_group": "4-6",
        "reading_level": "beginner",
        "text_content": (
            "Fynn the fox woke up early. He saw a bright blue butterfly near "
            "the forest path. Fynn followed it gently and found a tiny garden. "
            "He smiled and walked home before sunset."
        ),
    },
    {
        "title": "Mia's Moon Picnic",
        "age_group": "4-6",
        "reading_level": "beginner",
        "text_content": (
            "Mia packed apples, bread, and a red blanket. She sat with her puppy "
            "under the moon. They counted five shiny stars. Then Mia wished "
            "everyone a good night."
        ),
    },
    {
        "title": "The Little Seed",
        "age_group": "4-6",
        "reading_level": "beginner",
        "text_content": (
            "A little seed slept in the warm soil. Rain gave the seed a drink. "
            "The sun gave it light. Soon a yellow flower waved in the breeze."
        ),
    },
    {
        "title": "Adventures in Space",
        "age_group": "7-9",
        "reading_level": "intermediate",
        "text_content": (
            "Captain Mia checked every button in her small rocket ship. Beyond "
            "the window, planets glimmered like marbles. Her robot friend Atlas "
            "mapped a safe path through an asteroid field. Together they "
            "discovered a quiet purple moon."
        ),
    },
    {
        "title": "The Rainforest Rescue",
        "age_group": "7-9",
        "reading_level": "intermediate",
        "text_content": (
            "Nora heard a baby sloth calling from a tall rainforest tree. She "
            "and her guide built a gentle rope bridge. The sloth crossed safely "
            "to its mother. The whole forest seemed to cheer."
        ),
    },
    {
        "title": "The Inventor's Surprise",
        "age_group": "10-12",
        "reading_level": "advanced",
        "text_content": (
            "Arun carefully adjusted the gears inside his recycling machine. "
            "Instead of sorting paper, it began composing cheerful music. After "
            "testing each connection, Arun discovered that a loose copper wire "
            "had changed the program. He presented the musical invention at the "
            "school fair."
        ),
    },
)


def _account_specs():
    return (
        (
            "admin",
            "Site Admin",
            os.getenv("SEED_ADMIN_EMAIL", "admin@teachalike.app"),
            ROLE_ADMIN,
            os.getenv("SEED_ADMIN_PASSWORD", "ChangeMe123!"),
        ),
        (
            "parent",
            "Jamie Perera",
            os.getenv("SEED_PARENT_EMAIL", "jamie@teachalike.app"),
            ROLE_PARENT,
            os.getenv("SEED_PARENT_PASSWORD", "ParentDemo123!"),
        ),
        (
            "parent_2",
            "Priya Fernando",
            os.getenv("SEED_PARENT_2_EMAIL", "priya@teachalike.app"),
            ROLE_PARENT,
            os.getenv("SEED_PARENT_2_PASSWORD", "ParentDemo123!"),
        ),
        (
            "teacher",
            "Alex Silva",
            os.getenv("SEED_TEACHER_EMAIL", "alex.teacher@teachalike.app"),
            ROLE_TEACHER,
            os.getenv("SEED_TEACHER_PASSWORD", "TeacherDemo123!"),
        ),
    )


def _get_or_create_account(name, email, role, password):
    account = Parent.query.filter_by(email=email).first()
    if account:
        return account, False
    account = Parent(name=name, email=email, role=role)
    account.set_password(password)
    db.session.add(account)
    return account, True


def _get_or_create_child(parent, creator, name, age, gender, level, pin):
    child = Child.query.filter_by(parent_id=parent.id, name=name).first()
    if child:
        return child, False
    child = Child(
        parent_id=parent.id,
        created_by_id=creator.id,
        name=name,
        age=age,
        gender=gender,
        reading_level=level,
    )
    child.set_pin(pin)
    db.session.add(child)
    return child, True


def _get_or_create_book(values):
    book = Book.query.filter_by(title=values["title"]).first()
    if book:
        return book, False
    book = Book(**values)
    db.session.add(book)
    return book, True


def _get_or_create_session(
    child,
    book,
    *,
    completed,
    accuracy,
    minutes_ago,
):
    session = (
        ReadingSession.query.filter_by(child_id=child.id, book_id=book.id)
        .order_by(ReadingSession.id.asc())
        .first()
    )
    if session:
        return session, False

    started_at = utc_now() - timedelta(minutes=minutes_ago)
    session = ReadingSession(
        child_id=child.id,
        book_id=book.id,
        voice_profile_id=None,
        started_at=started_at,
        completed_at=started_at + timedelta(minutes=8) if completed else None,
        accuracy_score=accuracy,
        progress_log=[
            {
                "type": "pronunciation_check",
                "paragraph_index": 0,
                "accuracy": accuracy,
                "awarded_points": 10 if completed else 0,
            },
            {
                "type": "pronunciation_check",
                "paragraph_index": 1,
                "accuracy": max(0, accuracy - 3),
                "awarded_points": 10 if completed else 0,
            },
        ],
    )
    db.session.add(session)
    return session, True


def _ensure_feedback(session, feedback_type, text):
    existing = Feedback.query.filter_by(
        session_id=session.id,
        feedback_type=feedback_type,
    ).first()
    if existing:
        return False
    db.session.add(
        Feedback(
            session_id=session.id,
            feedback_type=feedback_type,
            feedback_text=text,
            audio_url=None,
        )
    )
    return True


def _ensure_game_result(child, game, score):
    if GameResult.query.filter_by(child_id=child.id, game_id=game.id).first():
        return False
    db.session.add(GameResult(child_id=child.id, game_id=game.id, score=score))
    return True


def _current_week_start():
    today = utc_now().date()
    return today - timedelta(days=today.weekday())


def _ensure_leaderboard_entry(child, points, streak_count):
    week_start = _current_week_start()
    entry = LeaderboardEntry.query.filter_by(
        child_id=child.id,
        week_start=week_start,
    ).first()
    if entry:
        return False
    db.session.add(
        LeaderboardEntry(
            child_id=child.id,
            week_start=week_start,
            points=points,
            streak_count=streak_count,
        )
    )
    return True


def seed_database():
    """Seed the active database and return creation counts and demo logins."""
    counts = {
        "accounts": 0,
        "children": 0,
        "books": 0,
        "mini_games_changed": 0,
        "reading_sessions": 0,
        "feedback_entries": 0,
        "game_results": 0,
        "leaderboard_entries": 0,
    }
    accounts = {}
    account_specs = _account_specs()

    try:
        for key, name, email, role, password in account_specs:
            accounts[key], created = _get_or_create_account(
                name,
                email,
                role,
                password,
            )
            counts["accounts"] += int(created)
        db.session.flush()

        child_specs = (
            (
                accounts["parent"],
                accounts["parent"],
                "Ava",
                6,
                "female",
                "beginner",
                "123456",
            ),
            (
                accounts["parent"],
                accounts["teacher"],
                "Noah",
                8,
                "male",
                "intermediate",
                "234567",
            ),
            (
                accounts["parent_2"],
                accounts["teacher"],
                "Maya",
                10,
                "female",
                "advanced",
                "345678",
            ),
            (
                accounts["parent_2"],
                accounts["parent_2"],
                "Leo",
                5,
                "male",
                "beginner",
                "456789",
            ),
        )
        children = []
        for spec in child_specs:
            child, created = _get_or_create_child(*spec)
            children.append(child)
            counts["children"] += int(created)
        db.session.flush()

        books = []
        for values in BOOKS:
            book, created = _get_or_create_book(values)
            books.append(book)
            counts["books"] += int(created)
        db.session.flush()

        for book in books:
            counts["mini_games_changed"] += len(
                create_default_mini_games(book, config={})
            )
        db.session.flush()

        quiz_games = {
            book.id: MiniGame.query.filter_by(
                book_id=book.id,
                game_type="quiz",
            ).first()
            for book in books
        }
        for child_index, child in enumerate(children):
            completed_session, created = _get_or_create_session(
                child,
                books[child_index % 3],
                completed=True,
                accuracy=92 - child_index * 4,
                minutes_ago=1440 + child_index * 30,
            )
            counts["reading_sessions"] += int(created)
            db.session.flush()

            counts["feedback_entries"] += int(
                _ensure_feedback(
                    completed_session,
                    "praise",
                    "Wonderful reading! You used a clear, confident voice.",
                )
            )
            counts["feedback_entries"] += int(
                _ensure_feedback(
                    completed_session,
                    "tip",
                    "Try pausing briefly at each full stop before you continue.",
                )
            )

            _, created = _get_or_create_session(
                child,
                books[(child_index % 3) + 3],
                completed=False,
                accuracy=84 - child_index * 3,
                minutes_ago=3 + child_index,
            )
            counts["reading_sessions"] += int(created)

            for book_index, score in (
                (child_index % 3, 30 + child_index * 4),
                ((child_index + 1) % 3, 42 + child_index * 3),
            ):
                game = quiz_games[books[book_index].id]
                if game:
                    counts["game_results"] += int(
                        _ensure_game_result(child, game, score)
                    )

            counts["leaderboard_entries"] += int(
                _ensure_leaderboard_entry(
                    child,
                    points=70 + child_index * 15,
                    streak_count=3 + child_index,
                )
            )

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    credentials = {
        key: {"email": email, "password": password}
        for key, _name, email, _role, password in account_specs
    }
    return counts, credentials


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        counts, credentials = seed_database()

    print("TeachAlike demo data is ready (no voices or images were seeded).")
    print(
        "Created/updated: "
        + ", ".join(f"{value} {key.replace('_', ' ')}" for key, value in counts.items())
        + "."
    )
    print("Demo logins:")
    for label, key in (
        ("Admin", "admin"),
        ("Parent", "parent"),
        ("Parent 2", "parent_2"),
        ("Teacher", "teacher"),
    ):
        print(
            f"  {label + ':':<10}"
            f"{credentials[key]['email']} / {credentials[key]['password']}"
        )
    print("Child PINs: Ava 123456, Noah 234567, Maya 345678, Leo 456789")


if __name__ == "__main__":
    main()
