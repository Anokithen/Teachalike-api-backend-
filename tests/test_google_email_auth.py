import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.auth_identity_model import AccountIdentity
from app.models.email_delivery_model import EMAIL_STATUS_RETRY, EMAIL_STATUS_SENT, EMAIL_TYPE_TEACHER_APPROVED, EmailDelivery
from app.models.email_verification_token_model import EmailVerificationToken
from app.models.parent_model import Parent, ROLE_ADMIN, ROLE_PARENT, ROLE_TEACHER
from app.models.teacher_application_model import APPROVAL_APPROVED, APPROVAL_PENDING, TeacherApplication
from app.services.auth_email_service import hash_token
from app.utils import utc_now


class GoogleEmailAuthTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.original_uri = Config.SQLALCHEMY_DATABASE_URI
        self.original_options = Config.SQLALCHEMY_ENGINE_OPTIONS
        self.original_google_client_id = Config.GOOGLE_AUTH_CLIENT_ID
        self.original_mail_transport = Config.MAIL_TRANSPORT
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.database_path}"
        Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        Config.GOOGLE_AUTH_CLIENT_ID = "google-client.test"
        Config.MAIL_TRANSPORT = "console"
        self.app = create_app()
        self.app.config.update(TESTING=True, GOOGLE_AUTH_CLIENT_ID="google-client.test", MAIL_TRANSPORT="console")
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        Config.SQLALCHEMY_DATABASE_URI = self.original_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = self.original_options
        Config.GOOGLE_AUTH_CLIENT_ID = self.original_google_client_id
        Config.MAIL_TRANSPORT = self.original_mail_transport
        os.unlink(self.database_path)

    def _account(self, role=ROLE_PARENT, email="user@example.com", verified=True):
        account = Parent(name="User", email=email, role=role, email_verified=verified)
        account.set_password("SecurePass123!")
        db.session.add(account)
        db.session.flush()
        return account

    def test_password_registration_requires_email_verification_and_hashes_token(self):
        response = self.client.post("/api/auth/register", json={
            "account_type": "parent",
            "name": "New Parent",
            "email": "New.Parent@example.com",
            "password": "SecurePass123!",
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["requires_email_verification"])
        self.assertNotIn("access_token", response.json)
        account = Parent.query.filter_by(email="new.parent@example.com").one()
        self.assertFalse(account.email_verified)
        token = EmailVerificationToken.query.filter_by(account_id=account.id).one()
        self.assertEqual(len(token.token_hash), 64)
        self.assertNotIn("token", token.to_dict())
        self.assertEqual(EmailDelivery.query.filter_by(recipient_account_id=account.id).count(), 1)

    def test_public_registration_cannot_create_teacher_or_admin(self):
        for role in ("teacher", "admin"):
            response = self.client.post("/api/auth/register", json={
                "account_type": role,
                "name": "Bad Role",
                "email": f"{role}@example.com",
                "password": "SecurePass123!",
            })
            self.assertEqual(response.status_code, 400, response.json)
            self.assertIsNone(Parent.query.filter_by(email=f"{role}@example.com").first())

    def test_verify_email_token_is_single_use_and_expiring(self):
        self.client.post("/api/auth/register", json={
            "account_type": "parent",
            "name": "Verify Me",
            "email": "verify@example.com",
            "password": "SecurePass123!",
        })
        token = EmailVerificationToken.query.one()
        raw = "known-token"
        token.token_hash = hash_token(raw)
        db.session.commit()
        ok = self.client.post("/api/auth/verify-email", json={"token": raw})
        self.assertEqual(ok.status_code, 200, ok.json)
        reused = self.client.post("/api/auth/verify-email", json={"token": raw})
        self.assertEqual(reused.status_code, 400, reused.json)

        account = self._account(email="expired@example.com", verified=False)
        expired = EmailVerificationToken(
            account_id=account.id,
            token_hash=hash_token("expired"),
            expires_at=utc_now() - timedelta(minutes=1),
        )
        db.session.add(expired)
        db.session.commit()
        response = self.client.post("/api/auth/verify-email", json={"token": "expired"})
        self.assertEqual(response.status_code, 400, response.json)

    def test_unverified_password_account_cannot_login(self):
        self._account(email="unverified@example.com", verified=False)
        db.session.commit()
        response = self.client.post("/api/auth/login", json={"email": "unverified@example.com", "password": "SecurePass123!"})
        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(response.json["code"], "EMAIL_NOT_VERIFIED")

    @patch("app.controllers.auth_controller._verify_google_credential")
    def test_google_login_creates_verified_parent_and_links_existing_role(self, verify_google):
        verify_google.return_value = {"sub": "google-sub-1", "email": "google@example.com", "email_verified": True, "name": "Google User"}
        response = self.client.post("/api/auth/google", json={"credential": "verified-id-token"})
        self.assertEqual(response.status_code, 200, response.json)
        account = Parent.query.filter_by(email="google@example.com").one()
        self.assertEqual(account.role, ROLE_PARENT)
        self.assertTrue(account.email_verified)
        self.assertEqual(AccountIdentity.query.filter_by(provider_subject="google-sub-1").one().account_id, account.id)

        teacher = self._account(role=ROLE_TEACHER, email="teacher.google@example.com")
        teacher.teacher_application = TeacherApplication(approval_status=APPROVAL_PENDING)
        db.session.commit()
        verify_google.return_value = {"sub": "google-sub-2", "email": "teacher.google@example.com", "email_verified": True, "name": "Teacher"}
        pending = self.client.post("/api/auth/google", json={"credential": "teacher-id-token"})
        self.assertEqual(pending.status_code, 403, pending.json)
        self.assertEqual(db.session.get(Parent, teacher.id).role, ROLE_TEACHER)

    @patch("app.controllers.admin_controller.send_delivery")
    def test_teacher_approval_email_is_idempotent_and_outage_safe(self, send_delivery_mock):
        admin = self._account(role=ROLE_ADMIN, email="admin@example.com")
        teacher = self._account(role=ROLE_TEACHER, email="teacher@example.com")
        teacher.teacher_application = TeacherApplication(approval_status=APPROVAL_PENDING)
        db.session.commit()
        from flask_jwt_extended import create_access_token
        headers = {"Authorization": f"Bearer {create_access_token(identity=admin.id)}"}

        send_delivery_mock.side_effect = RuntimeError("gmail outage")
        response = self.client.patch(f"/api/admin/teachers/{teacher.id}/approve", headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(EmailDelivery.query.filter_by(email_type=EMAIL_TYPE_TEACHER_APPROVED).count(), 1)
        repeat = self.client.patch(f"/api/admin/teachers/{teacher.id}/approve", headers=headers)
        self.assertEqual(repeat.status_code, 200, repeat.json)
        self.assertEqual(EmailDelivery.query.filter_by(email_type=EMAIL_TYPE_TEACHER_APPROVED).count(), 1)


if __name__ == "__main__":
    unittest.main()
