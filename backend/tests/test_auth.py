"""Auth API regressions using synthetic settings and an isolated SQLite database.

Application settings are synthetic and do not depend on the local .env.
Run from backend with: python -m unittest discover -s tests -v
"""

import io
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
}):
    from app.api.routes.auth import router
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
