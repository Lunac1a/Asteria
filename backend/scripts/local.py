"""Local-only setup/launch. Never reads or modifies an existing remote database."""

import argparse
import os
import secrets
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["configure", "init", "prepare", "serve"])
    parser.add_argument("--env-file", default=".env.local-mvp")
    parser.add_argument(
        "--test-ai", action="store_true", help="Explicit simulated embeddings and LLM"
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    path = Path(args.env_file).resolve()
    if path.parent != ROOT:
        raise SystemExit("Use an environment file inside backend/.")
    if args.command == "configure":
        if path.exists():
            raise SystemExit("Environment file exists; preserved without changes.")
        from cryptography.fernet import Fernet

        values = {
            "APP_NAME": "Asteria Local",
            "APP_HOST": "127.0.0.1",
            "APP_PORT": "18001",
            "FRONTEND_URL": "http://localhost:3001",
            "DATABASE_URL": "postgresql+psycopg://asteria:local-development-only@127.0.0.1:55432/asteria_local",
            "JWT_SECRET_KEY": secrets.token_urlsafe(48),
            "JWT_ALGORITHM": "HS256",
            "JWT_EXPIRE_MINUTES": "120",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "LOCAL_DATA_DIR": "../data/local-mvp",
            "EMBEDDING_BACKEND": "test" if args.test_ai else "fastembed",
            "EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
            "LLM_BACKEND": "test" if args.test_ai else "provider",
            "LLM_ALLOWED_BASE_URLS": "https://integrate.api.nvidia.com/v1",
            "RETRIEVAL_MIN_SCORE": "0.15" if args.test_ai else "0.45",
        }
        path.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )
        print("Local configuration created. Generated secrets are not printed.")
        return
    if not path.exists():
        raise SystemExit(
            "Run configure first or specify an existing local environment file."
        )
    os.environ.update(
        {key: value for key, value in dotenv_values(path).items() if value is not None}
    )
    from app.core.config import settings
    from sqlalchemy.engine import make_url

    url = make_url(settings.DATABASE_URL)
    if url.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    } or not url.drivername.startswith("postgresql"):
        raise SystemExit(
            "Local MVP commands require loopback PostgreSQL. No database changes made."
        )
    from runpy import run_module

    if args.command == "init":
        run_module("app.db.init_local", run_name="__main__")
    elif args.command == "prepare":
        run_module("app.services.prepare_embeddings", run_name="__main__")
    else:
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=settings.APP_PORT,
            workers=1,
            access_log=False,
            limit_concurrency=16,
            timeout_keep_alive=5,
        )


if __name__ == "__main__":
    main()
