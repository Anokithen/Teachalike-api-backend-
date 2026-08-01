"""Seed one administrator and two parent accounts.

Run this file from the API project root after configuring the database::

    python seed.py

The seed is additive and idempotent. Existing accounts are preserved, including
their current passwords, and no children, teachers, books, or activity data are
created. Override the demo credentials with the corresponding ``SEED_*``
environment variables when needed.
"""

import os

from app import create_app
from app.extensions import db
from app.models.parent_model import ROLE_ADMIN, ROLE_PARENT, Parent


def _user_specs():
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
    )


def _get_or_create_user(name, email, role, password):
    user = Parent.query.filter_by(email=email).first()
    if user is not None:
        return user, False

    user = Parent(name=name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    return user, True


def seed_database():
    """Create the three demo accounts and return the count and credentials."""
    created_count = 0
    user_specs = _user_specs()

    try:
        for _key, name, email, role, password in user_specs:
            _user, created = _get_or_create_user(name, email, role, password)
            created_count += int(created)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    credentials = {
        key: {"email": email, "password": password}
        for key, _name, email, _role, password in user_specs
    }
    return {"accounts": created_count}, credentials


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        counts, credentials = seed_database()

    print("TeachAlike user seed is ready.")
    print(f"Created {counts['accounts']} account(s).")
    print("Demo logins:")
    for label, key in (
        ("Admin", "admin"),
        ("Parent", "parent"),
        ("Parent 2", "parent_2"),
    ):
        print(
            f"  {label + ':':<10}"
            f"{credentials[key]['email']} / {credentials[key]['password']}"
        )


if __name__ == "__main__":
    main()
