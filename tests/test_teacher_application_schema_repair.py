"""Regression coverage for additive teacher application schema repair."""

import os
import tempfile
import unittest

from flask import request
from flask_jwt_extended import create_access_token
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from app import (
    DATABASE_SCHEMA_NOT_READY_PAYLOAD,
    TEACHER_APPLICATION_REQUIRED_COLUMNS,
    _ensure_teacher_application_schema,
    _verify_database_schema,
    create_app,
)
from app.config import Config
from app.extensions import db
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_TEACHER


class TeacherApplicationSchemaRepairTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.original_uri = Config.SQLALCHEMY_DATABASE_URI
        self.original_options = Config.SQLALCHEMY_ENGINE_OPTIONS
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

        self.admin = self._account("Repair Admin", "repair.admin@example.com", ROLE_ADMIN)
        self.pending = self._account("Pending Teacher", "pending.repair@example.com", ROLE_TEACHER)
        self.rejected = self._account("Rejected Teacher", "rejected.repair@example.com", ROLE_TEACHER)
        self.legacy = self._account("Legacy Teacher", "legacy.repair@example.com", ROLE_TEACHER)
        db.session.commit()
        self.admin_id = self.admin.id
        self.pending_id = self.pending.id
        self.rejected_id = self.rejected.id
        self.legacy_id = self.legacy.id
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        Config.SQLALCHEMY_DATABASE_URI = self.original_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = self.original_options
        os.unlink(self.database_path)

    @staticmethod
    def _account(name, email, role):
        account = Parent(name=name, email=email, role=role, is_banned=False)
        account.set_password("SecurePass123!")
        db.session.add(account)
        return account

    def _headers(self):
        admin = db.session.get(Parent, self.admin_id)
        return {"Authorization": f"Bearer {create_access_token(identity=admin.id)}"}

    def _drop_application_table(self):
        db.session.remove()
        db.session.execute(text("DROP TABLE teacher_applications"))
        db.session.commit()

    def _create_partial_application_table(self):
        self._drop_application_table()
        db.session.execute(text(
            "CREATE TABLE teacher_applications ("
            "account_id INTEGER NULL, approval_status VARCHAR(20) NULL)"
        ))
        db.session.execute(text(
            "INSERT INTO teacher_applications (account_id, approval_status) "
            "VALUES (:pending_id, 'pending'), (:rejected_id, 'rejected')"
        ), {
            "pending_id": self.pending_id,
            "rejected_id": self.rejected_id,
        })
        db.session.commit()

    def test_missing_table_is_created_and_legacy_teachers_are_approved(self):
        self._drop_application_table()

        _ensure_teacher_application_schema()

        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table("teacher_applications"))
        columns = {
            column["name"]
            for column in inspector.get_columns("teacher_applications")
        }
        self.assertTrue(TEACHER_APPLICATION_REQUIRED_COLUMNS.issubset(columns))
        statuses = dict(db.session.execute(text(
            "SELECT account_id, approval_status FROM teacher_applications"
        )).all())
        self.assertEqual(statuses[self.pending_id], "approved")
        self.assertEqual(statuses[self.rejected_id], "approved")
        self.assertEqual(statuses[self.legacy_id], "approved")

    def test_partial_table_adds_columns_and_preserves_review_statuses(self):
        self._create_partial_application_table()

        _ensure_teacher_application_schema()
        _ensure_teacher_application_schema()

        inspector = inspect(db.engine)
        columns = {
            column["name"]
            for column in inspector.get_columns("teacher_applications")
        }
        self.assertTrue(TEACHER_APPLICATION_REQUIRED_COLUMNS.issubset(columns))
        statuses = dict(db.session.execute(text(
            "SELECT account_id, approval_status FROM teacher_applications"
        )).all())
        self.assertEqual(statuses[self.pending_id], "pending")
        self.assertEqual(statuses[self.rejected_id], "rejected")
        self.assertEqual(statuses[self.legacy_id], "approved")
        unique_account_keys = [
            index for index in inspector.get_indexes("teacher_applications")
            if index.get("unique")
            and index.get("column_names") == ["account_id"]
        ]
        self.assertTrue(unique_account_keys)
        indexes_by_columns = {
            tuple(index.get("column_names") or ())
            for index in inspector.get_indexes("teacher_applications")
        }
        self.assertIn(("id",), indexes_by_columns)
        self.assertIn(("approval_status",), indexes_by_columns)
        self.assertIn(("reviewed_by_id",), indexes_by_columns)
        _verify_database_schema()

    def test_readiness_returns_503_for_partial_application_schema(self):
        self._create_partial_application_table()

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["database"], "unavailable")

    def test_pending_admin_list_returns_200_after_partial_schema_repair(self):
        self._create_partial_application_table()
        _ensure_teacher_application_schema()

        response = self.client.get(
            "/api/admin/teachers?status=pending",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(
            [teacher["id"] for teacher in response.json["teachers"]],
            [self.pending_id],
        )

    def test_approve_and_reject_work_after_partial_schema_repair(self):
        self._create_partial_application_table()
        _ensure_teacher_application_schema()
        headers = self._headers()

        approved = self.client.patch(
            f"/api/admin/teachers/{self.pending_id}/approve",
            headers=headers,
        )
        rejected = self.client.patch(
            f"/api/admin/teachers/{self.legacy_id}/reject",
            headers=headers,
            json={"reason": "Application details need revision."},
        )

        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(rejected.status_code, 200, rejected.json)
        statuses = dict(db.session.execute(text(
            "SELECT account_id, approval_status FROM teacher_applications"
        )).all())
        self.assertEqual(statuses[self.pending_id], "approved")
        self.assertEqual(statuses[self.legacy_id], "rejected")

    def test_duplicate_accounts_fail_repair_without_deleting_rows(self):
        self._drop_application_table()
        db.session.execute(text(
            "CREATE TABLE teacher_applications ("
            "id INTEGER, account_id INTEGER, approval_status VARCHAR(20), "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        db.session.execute(text(
            "INSERT INTO teacher_applications "
            "(id, account_id, approval_status, created_at, updated_at) VALUES "
            "(1, :account_id, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
            "(2, :account_id, 'rejected', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"account_id": self.pending_id})
        db.session.commit()

        with self.assertRaisesRegex(RuntimeError, "duplicate rows"):
            _ensure_teacher_application_schema()

        count = db.session.execute(text(
            "SELECT COUNT(*) FROM teacher_applications"
        )).scalar_one()
        self.assertEqual(count, 2)

    def test_missing_ids_are_allocated_without_colliding_with_existing_ids(self):
        self._drop_application_table()
        db.session.execute(text(
            "CREATE TABLE teacher_applications ("
            "id INTEGER, account_id INTEGER, approval_status VARCHAR(20), "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        db.session.execute(text(
            "INSERT INTO teacher_applications "
            "(id, account_id, approval_status, created_at, updated_at) VALUES "
            "(:existing_id, :pending_id, 'pending', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP), (NULL, :rejected_id, 'rejected', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {
            "existing_id": self.rejected_id,
            "pending_id": self.pending_id,
            "rejected_id": self.rejected_id,
        })
        db.session.commit()

        _ensure_teacher_application_schema()

        ids = db.session.execute(text(
            "SELECT id FROM teacher_applications WHERE account_id IN "
            "(:pending_id, :rejected_id) ORDER BY account_id"
        ), {
            "pending_id": self.pending_id,
            "rejected_id": self.rejected_id,
        }).scalars().all()
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertGreater(max(ids), self.rejected_id)

    def test_mysql_schema_errors_return_safe_response(self):
        class DriverError(Exception):
            def __init__(self, code):
                self.args = (code, "sensitive driver detail")

        endpoint = "/test-only/schema-error"

        def raise_schema_error():
            code = int(request.args["code"])
            raise ProgrammingError("sensitive sql", {}, DriverError(code))

        self.app.add_url_rule(endpoint, endpoint, raise_schema_error)
        for code in (1054, 1146):
            with self.subTest(code=code):
                response = self.client.get(f"{endpoint}?code={code}")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json, DATABASE_SCHEMA_NOT_READY_PAYLOAD)
                self.assertNotIn(
                    "sensitive", response.get_data(as_text=True).lower()
                )

        other_error = self.client.get(f"{endpoint}?code=1064")
        self.assertEqual(other_error.status_code, 500)
        self.assertEqual(other_error.json["error_code"], "DATABASE_QUERY_FAILED")
        self.assertNotIn("invalid database", other_error.json["error"].lower())


if __name__ == "__main__":
    unittest.main()
