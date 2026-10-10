"""Bounded real-provider learning trial. Synthetic account; no secrets in artifacts."""

# Environment must be loaded before importing application settings/models.
# ruff: noqa: E402
import argparse
import json
import os
from pathlib import Path
import time
import sys
import uuid
from datetime import datetime, timezone
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
sys.stdout.reconfigure(encoding="utf-8")
os.chdir(ROOT / "backend")
os.environ.update({k: v for k, v in dotenv_values(".env").items() if v is not None})
from app.main import create_app  # registers models
from app.db.session import SessionLocal
from app.models.users import User
from app.models.user_llm_settings import UserLLMSetting
from app.models.knowledge import Workspace, Document, MessageEvidence
from app.services.knowledge_service import data_root
from app.models.chat_sessions import ChatSession
from app.models.messages import Message
from app.core.security import hash_password, create_access_token

POINTER = ROOT / "data" / "learning-trial-current.json"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["start", "turn", "upload", "upload-existing", "finish"]
    )
    parser.add_argument(
        "--source-workspace",
        help="Copy this authorized workspace's files into an isolated trial using its owner's provider",
    )
    parser.add_argument("--message")
    parser.add_argument("--file")
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Call the same API in-process, capturing only this synthetic trial's real provider output",
    )
    args = parser.parse_args()
    if args.command == "start":
        if POINTER.exists():
            raise SystemExit("An unfinished trial already exists; preserve it.")
        with SessionLocal() as db:
            source = (
                db.get(Workspace, args.source_workspace)
                if args.source_workspace
                else None
            )
            if args.source_workspace and source is None:
                raise SystemExit("Source workspace does not exist")
            setting = (
                db.query(UserLLMSetting).filter_by(user_id=source.user_id).first()
                if source
                else db.query(UserLLMSetting)
                .order_by(UserLLMSetting.updated_at.desc())
                .first()
            )
            if not setting:
                raise SystemExit("No configured provider")
            uid = uuid.uuid4()
            email = f"guidance-trial-{uid.hex}@example.com"
            db.add(
                User(id=uid, email=email, password_hash=hash_password(uuid.uuid4().hex))
            )
            db.flush()
            db.add(
                UserLLMSetting(
                    id=str(uuid.uuid4()),
                    user_id=uid,
                    provider=setting.provider,
                    encrypted_api_key=setting.encrypted_api_key,
                    model_name=setting.model_name,
                    base_url=setting.base_url,
                )
            )
            workspace = str(uuid.uuid4())
            db.add(
                Workspace(
                    id=workspace, user_id=uid, name="真实模型学习引导验收（临时）"
                )
            )
            db.commit()
            folder = (
                ROOT
                / "data"
                / (
                    "learning-trial-"
                    + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                )
            )
            folder.mkdir()
            state = dict(
                user_id=str(uid),
                email=email,
                workspace_id=workspace,
                session_id=None,
                model=setting.model_name,
                turns=[],
                documents=[],
                source_workspace=source.id if source else None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            save(folder / "trial.json", state)
            save(POINTER, {"folder": str(folder)})
            print(
                json.dumps(
                    {"model": state["model"], "report": str(folder)}, ensure_ascii=False
                )
            )
        return
    folder = Path(json.loads(POINTER.read_text(encoding="utf-8"))["folder"]).resolve()
    if not folder.is_relative_to((ROOT / "data").resolve()):
        raise SystemExit("Invalid report directory")
    state = json.loads((folder / "trial.json").read_text(encoding="utf-8"))
    uid = uuid.UUID(state["user_id"])
    with SessionLocal() as db:
        user = db.get(User, uid)
        if (
            not user
            or user.email != state["email"]
            or not user.email.startswith("guidance-trial-")
        ):
            raise SystemExit("Synthetic account identity mismatch")
    token = create_access_token({"sub": str(uid)})
    with httpx.Client(
        base_url="http://127.0.0.1:18001/api/",
        headers={"Authorization": "Bearer " + token},
        timeout=100,
    ) as client:
        if args.command == "turn":
            if not args.message or len(state["turns"]) >= 12:
                raise SystemExit("Message required; trial capped at 12 turns")
            entry = dict(
                number=len(state["turns"]) + 1,
                question=args.message,
                request_id=str(uuid.uuid4()),
            )
            state["turns"].append(entry)
            save(folder / "trial.json", state)
            started = time.perf_counter()
            try:
                payload = dict(
                    message=args.message,
                    workspace_id=state["workspace_id"],
                    session_id=state["session_id"],
                    session_type="learning" if not state["session_id"] else None,
                    request_id=entry["request_id"],
                )
                if args.capture:
                    from unittest.mock import patch
                    from fastapi.testclient import TestClient
                    from app.services.rag_service import provider_completion

                    def capture(*values, **kwargs):
                        assert str(values[1]) == str(uid)
                        result = provider_completion(*values, **kwargs)
                        phase = kwargs.get("metric_phase", "generation")
                        observed = dict(
                            phase=phase,
                            raw=str(result),
                            metadata=getattr(result, "metadata", {}),
                        )
                        supplied = json.loads(values[3])
                        if phase == "generation":
                            observed["input_evidence"] = supplied.get("evidence", [])
                        elif phase == "citation_audit":
                            observed["audit_input"] = supplied
                        entry.setdefault("provider_outputs", []).append(observed)
                        return result

                    with (
                        patch(
                            "app.services.rag_service.provider_completion",
                            side_effect=capture,
                        ),
                        patch(
                            "app.services.learning_knowledge.provider_completion",
                            side_effect=capture,
                        ),
                        TestClient(create_app()) as observed,
                    ):
                        response = observed.post(
                            "/api/chat",
                            json=payload,
                            headers={"Authorization": "Bearer " + token},
                        )
                else:
                    response = client.post("chat", json=payload)
                entry.update(
                    status=response.status_code,
                    elapsed_seconds=round(time.perf_counter() - started, 2),
                    response=response.json(),
                )
                if response.is_success:
                    state["session_id"] = response.json()["session_id"]
                    notes = client.get(f"chat/sessions/{state['session_id']}/learning")
                    entry["learning"] = notes.json()
                    with SessionLocal() as db:
                        message = (
                            db.query(Message)
                            .filter_by(session_id=state["session_id"], role="assistant")
                            .order_by(Message.created_at.desc(), Message.id.desc())
                            .first()
                        )
                        evidence = (
                            db.get(MessageEvidence, message.id) if message else None
                        )
                        if evidence:
                            entry["evidence"] = dict(
                                sources=evidence.sources, policy=evidence.ai_mode
                            )
            except Exception as exc:
                entry.update(
                    error=type(exc).__name__,
                    elapsed_seconds=round(time.perf_counter() - started, 2),
                )
            save(folder / "trial.json", state)
            print(
                json.dumps(
                    dict(
                        number=entry["number"],
                        status=entry.get("status"),
                        elapsed_seconds=entry.get("elapsed_seconds"),
                        answer=entry.get("response", {}).get("answer"),
                        error=entry.get("response", {}).get(
                            "detail", entry.get("error")
                        ),
                        next_step=entry.get("learning", {}).get("next_step"),
                        focus=entry.get("learning", {}).get("focus"),
                        basis=entry.get("response", {}).get("answer_basis"),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "upload-existing":
            if not state.get("source_workspace") or state["documents"]:
                raise SystemExit(
                    "Existing materials require a fresh trial with a source workspace"
                )
            with SessionLocal() as db:
                docs = (
                    db.query(Document)
                    .filter_by(workspace_id=state["source_workspace"])
                    .order_by(Document.created_at, Document.id)
                    .all()
                )
                if not 1 <= len(docs) <= 10:
                    raise SystemExit("Expected 1-10 source documents")
                root = data_root().resolve()
                copies = []
                for doc in docs:
                    original = (root / doc.storage_key).resolve()
                    if (
                        not original.is_relative_to(root / "uploads")
                        or not original.is_file()
                        or Path(doc.name).name != doc.name
                    ):
                        raise SystemExit("Invalid source file path")
                    copy = folder / doc.name
                    if copy.exists():
                        raise SystemExit("Duplicate material name")
                    copy.write_bytes(original.read_bytes())
                    copies.append((doc.id, copy))
            for source_id, file in copies:
                response = client.post(
                    f"workspaces/{state['workspace_id']}/documents",
                    files={"file": (file.name, file.read_bytes(), "text/markdown")},
                )
                state["documents"].append(
                    dict(
                        source_document_id=source_id,
                        status=response.status_code,
                        response=response.json(),
                    )
                )
                save(folder / "trial.json", state)
                response.raise_for_status()
                print(json.dumps(state["documents"][-1], ensure_ascii=False))
        elif args.command == "upload":
            file = Path(args.file).resolve()
            if not file.is_relative_to((ROOT / "data").resolve()):
                raise SystemExit("Only synthetic trial files under data are allowed")
            response = client.post(
                f"workspaces/{state['workspace_id']}/documents",
                files={"file": (file.name, file.read_bytes(), "text/plain")},
            )
            state["documents"].append(
                dict(status=response.status_code, response=response.json())
            )
            save(folder / "trial.json", state)
            print(json.dumps(state["documents"][-1], ensure_ascii=False))
        else:
            response = client.delete(f"workspaces/{state['workspace_id']}")
            response.raise_for_status()
            with SessionLocal() as db:
                assert not db.query(Workspace).filter_by(user_id=uid).count()
                assert not db.query(ChatSession).filter_by(user_id=uid).count()
                db.query(UserLLMSetting).filter_by(user_id=uid).delete()
                db.delete(db.get(User, uid))
                db.commit()
            state["cleaned_up"] = True
            save(folder / "trial.json", state)
            POINTER.unlink()
            print(
                "Synthetic trial account removed; transcripts retained at "
                + str(folder)
            )


if __name__ == "__main__":
    main()
