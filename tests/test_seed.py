"""The seed creates only one admin and two parent accounts."""

import os
import tempfile
import unittest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.parent_model import ROLE_ADMIN, ROLE_PARENT, Parent
from seed import seed_database


class SeedDataTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        os.unlink(self.database_path)

    def test_seed_is_idempotent_and_creates_only_requested_users(self):
        first_counts, credentials = seed_database()
        second_counts, _credentials = seed_database()

        self.assertEqual(first_counts, {"accounts": 3})
        self.assertEqual(second_counts, {"accounts": 0})
        self.assertEqual(Parent.query.count(), 3)
        self.assertEqual(
            Parent.query.filter_by(role=ROLE_ADMIN).count(),
            1,
        )
        self.assertEqual(
            Parent.query.filter_by(role=ROLE_PARENT).count(),
            2,
        )

        for key in ("admin", "parent", "parent_2"):
            user = Parent.query.filter_by(email=credentials[key]["email"]).one()
            self.assertTrue(user.check_password(credentials[key]["password"]))
            self.assertIsNone(user.profile_image_url)
            self.assertIsNone(user.profile_image_public_id)

        for table in db.metadata.sorted_tables:
            if table.name == Parent.__tablename__:
                continue
            row_count = db.session.execute(
                db.select(db.func.count()).select_from(table)
            ).scalar_one()
            self.assertEqual(row_count, 0, f"Unexpected rows in {table.name}")


if __name__ == "__main__":
    unittest.main()
