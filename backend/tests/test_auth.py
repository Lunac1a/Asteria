"""Auth API regressions using synthetic settings and an isolated SQLite database.

Application settings are synthetic and do not depend on the local .env.
Run from backend with: python -m unittest discover -s tests -v
"""

import base64
import io
import json
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Override every application setting before importing any application module.
# No production database, provider key, JWT secret or encryption key is used.
with patch.dict("os.environ", {
    "APP_NAME": "Asteria auth tests",
    "APP_PORT": "8000",
    "FRONTEND_URL": "http://testserver",
    "DATABASE_URL": "sqlite://",
    "JWT_SECRET_KEY": "synthetic-test-secret-never-use-in-production",
    "JWT_ALGORITHM": "HS256",
    "JWT_EXPIRE_MINUTES": "60",
    "LOCAL_DATA_DIR": "../data/test-runs",
    "EMBEDDING_BACKEND": "test",
    "EMBEDDING_MODEL": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "LLM_BACKEND": "test",
    "LLM_ALLOWED_BASE_URLS": "https://integrate.api.nvidia.com/v1",
    "PROVIDER_IS_SIMULATED": "false",
    "RUNTIME_METRICS_ENABLED": "false",
    "CITATION_AUDIT_ENABLED": "false",
    "CITATION_AUDIT_TIMEOUT_SECONDS": "18",
    "GENERATION_JSON_SCHEMA_PROFILES": "[]",
    "RETRIEVAL_MIN_SCORE": "0.35",
    "RETRIEVAL_LEXICAL_RESCUE": "true",
    "INDEX_TIMEOUT_SECONDS": "90",
    "QUERY_TIMEOUT_SECONDS": "30",
    "MAX_UPLOAD_BYTES": "10485760",
    "MAX_WORKSPACE_CHUNKS": "2000",
    "RECOVERY_ADMIN_USER_IDS": "[]",
    "RECOVERY_SINGLE_PROCESS_CONFIRMED": "false",
    "WORKSPACE_DELETE_MAINTENANCE_MODE": "false",
    "STARTUP_RECOVERY_ENABLED": "false",
    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
}):
    from app.api.routes.auth import router
    from app.core.config import settings
    from app.core.security import verify_password
    from app.db.base import Base
    from app.db.session import get_db
    from app.models.user_llm_settings import UserLLMSetting  # noqa: F401
    from app.models.users import User


class AuthAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

        def isolated_db():
            with self.session_factory() as db:
                yield db

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = isolated_db
        self.client = self.enterContext(TestClient(app))
        self.email = "student@example.com"
        self.password = "SyntheticPassword-42"

    def register_and_login(self, email):
        payload = {"email": email, "password": self.password}
        registered = self.client.post("/api/register", json=payload)
        self.assertEqual(registered.status_code, 200)
        login = self.client.post("/api/login", json=payload)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["token_type"], "bearer")
        return registered.json(), login.json()["access_token"]

    def read_me(self, token):
        return self.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    def assert_auth_rejected(self, response, detail="Could not validate credentials"):
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")
        # Exact generic response excludes identity, credentials and token details.
        self.assertEqual(response.json(), {"detail": detail})

    def test_jwt_valid_login_token_returns_current_user(self):
        user, token = self.register_and_login(self.email)
        response = self.read_me(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"id", "email", "created_at"})
        self.assertEqual(response.json()["id"], user["id"])
        self.assertEqual(response.json()["email"], user["email"])

    def test_jwt_tampered_subject_with_original_signature_is_rejected(self):
        user_a, token_a = self.register_and_login(self.email)
        user_b, token_b = self.register_and_login("other@example.com")
        self.assertNotEqual(user_a["id"], user_b["id"])
        for user, token in [(user_a, token_a), (user_b, token_b)]:
            response = self.read_me(token)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], user["id"])
            self.assertEqual(response.json()["email"], user["email"])
            with self.session_factory() as db:
                self.assertIsNotNone(db.get(User, UUID(user["id"])))

        header, payload, signature = token_a.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.assertEqual(claims["sub"], user_a["id"])
        claims["sub"] = user_b["id"]
        changed_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        tampered = f"{header}.{changed_payload}.{signature}"
        self.assertNotEqual(tampered, token_a)
        self.assertEqual(tampered.split(".")[2], signature)
        self.assertEqual(jwt.get_unverified_claims(tampered)["sub"], user_b["id"])
        self.assert_auth_rejected(self.read_me(tampered))

    def test_jwt_expired_correctly_signed_token_is_rejected(self):
        user, token = self.register_and_login(self.email)
        claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        self.assertEqual(claims["sub"], user["id"])
        claims["exp"] = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp())
        expired = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        # Only expiry verification is disabled here to prove the fixture's signature
        # and other claims are valid. The HTTP request uses normal verification.
        self.assertEqual(jwt.decode(
            expired, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        ), claims)
        with self.session_factory() as db:
            self.assertIsNotNone(db.get(User, UUID(user["id"])))
        self.assertEqual(self.read_me(token).status_code, 200)
        self.assert_auth_rejected(self.read_me(expired))

    def test_jwt_valid_token_for_deleted_user_is_rejected(self):
        user, token = self.register_and_login(self.email)
        self.assertEqual(self.read_me(token).status_code, 200)
        with self.session_factory() as db:
            stored = db.get(User, UUID(user["id"]))
            self.assertIsNotNone(stored)
            db.delete(stored)
            db.commit()
        with self.session_factory() as db:
            self.assertIsNone(db.get(User, UUID(user["id"])))
        claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        self.assertEqual(claims["sub"], user["id"])
        self.assert_auth_rejected(self.read_me(token))

    def test_jwt_missing_authorization_header_is_rejected(self):
        self.assert_auth_rejected(self.client.get("/api/me"), detail="Not authenticated")

    def test_registration_keeps_password_private_and_allows_login(self):
        stdout, stderr, logs = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        logger = logging.getLogger()
        logger.addHandler(handler)
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                response = self.client.post("/api/register", json={
                    "email": self.email, "password": self.password,
                })
        finally:
            logger.removeHandler(handler)
            handler.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"id", "email"})
        self.assertEqual(response.json()["email"], self.email)
        output = stdout.getvalue() + stderr.getvalue() + logs.getvalue()
        self.assertTrue(
            self.password not in output + response.text,
            "Registration exposed the submitted password",
        )

        with self.session_factory() as db:
            user = db.query(User).filter_by(email=self.email).one()
            self.assertNotEqual(user.password_hash, self.password)
            self.assertTrue(verify_password(self.password, user.password_hash))

        login = self.client.post("/api/login", json={
            "email": self.email, "password": self.password,
        })
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["token_type"], "bearer")
        me = self.client.get("/api/me", headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
        })
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["id"], response.json()["id"])
        self.assertNotIn("password_hash", me.json())

        wrong_password = self.client.post("/api/login", json={
            "email": self.email, "password": "WrongSyntheticPassword",
        })
        self.assertEqual(wrong_password.status_code, 400)
        self.assertNotIn("access_token", wrong_password.json())

    def test_duplicate_email_is_rejected_without_creating_another_user(self):
        payload = {"email": self.email, "password": self.password}
        self.assertEqual(self.client.post("/api/register", json=payload).status_code, 200)
        response = self.client.post("/api/register", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Email already registered")
        with self.session_factory() as db:
            self.assertEqual(db.query(User).count(), 1)

    def test_short_password_is_rejected_without_creating_a_user(self):
        response = self.client.post("/api/register", json={
            "email": self.email, "password": "short",
        })
        self.assertEqual(response.status_code, 422)
        with self.session_factory() as db:
            self.assertEqual(db.query(User).count(), 0)
