import threading
import re
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat_sessions import ChatSession
from app.models.messages import Message
from app.models.knowledge import SessionWorkspace, MessageEvidence, Document
from app.models.chat_turn import ChatTurn
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.knowledge_service import owned_workspace, retrieve
from app.services.rag_service import grounded_answer

router = APIRouter()
chat_slot = threading.BoundedSemaphore(1)


def mode():
    return f"embedding={settings.EMBEDDING_BACKEND};llm={settings.LLM_BACKEND}"


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not chat_slot.acquire(blocking=False):
        raise HTTPException(
            429, "A chat is already running locally. Please try again shortly."
        )
    try:
        existing = db.get(ChatTurn, str(payload.request_id))
        if existing:
            binding = db.get(SessionWorkspace, existing.session_id)
            if (
                existing.user_id != user_id
                or existing.question != payload.message.strip()
                or not binding
                or binding.workspace_id != payload.workspace_id
                or (payload.session_id and payload.session_id != existing.session_id)
            ):
                raise HTTPException(409, "Request ID already used for a different turn")
            return existing.response
        if not payload.workspace_id:
            raise HTTPException(422, "Select a Workspace before asking about materials")
        owned_workspace(db, payload.workspace_id, user_id)
        question = payload.message.strip()
        if not question:
            raise HTTPException(422, "Question cannot be blank")
        session = None
        history = []
        if payload.session_id:
            session = (
                db.query(ChatSession)
                .filter_by(id=payload.session_id, user_id=user_id)
                .first()
            )
            binding = db.get(SessionWorkspace, payload.session_id)
            if (
                not session
                or not binding
                or binding.workspace_id != payload.workspace_id
            ):
                raise HTTPException(404, "Session not found in this Workspace")
            # Each completed turn has distinct timestamps, including on PostgreSQL.
            recent = (
                db.query(Message)
                .filter_by(session_id=session.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(12)
                .all()
            )
            budget = 12000
            for message in recent:
                if len(message.content) > budget:
                    break
                history.insert(0, {"role": message.role, "content": message.content})
                budget -= len(message.content)
        search_question = question
        # Include the last user question for short follow-ups such as "why?".
        followup = re.search(
            r"\b(it|its|that|those|they|their|this|these)\b|^(why\??|explain more|go on)$|这|那|它|继续|^为什么[？?]?$",
            question,
            re.IGNORECASE,
        )
        if len(question) < 100 and history and followup:
            previous = next(
                (m["content"] for m in reversed(history) if m["role"] == "user"), ""
            )
            search_question = previous[:1000] + " " + question
        sources = retrieve(db, payload.workspace_id, search_question)
        # End the read transaction before waiting on the external provider.
        db.rollback()
        answer, selected = grounded_answer(db, user_id, question, history, sources)
        if not session:
            session = ChatSession(
                id=str(uuid.uuid4()), user_id=user_id, title=question[:60]
            )
            db.add(session)
            db.flush()
            db.add(
                SessionWorkspace(
                    session_id=session.id, workspace_id=payload.workspace_id
                )
            )
        # Revalidate sources in case a document was deleted during generation.
        current_ids = {
            row[0]
            for row in db.query(Document.id)
            .filter(
                Document.workspace_id == payload.workspace_id,
                Document.status == "ready",
            )
            .all()
        }
        if any(source["document_id"] not in current_ids for source in selected):
            raise HTTPException(
                409, "Source documents changed during the answer. Please retry."
            )
        now = datetime.now(timezone.utc)
        user_message = Message(
            id=str(uuid.uuid4()),
            session_id=session.id,
            role="user",
            content=question,
            created_at=now,
        )
        assistant = Message(
            id=str(uuid.uuid4()),
            session_id=session.id,
            role="assistant",
            content=answer,
            created_at=now + timedelta(microseconds=1),
        )
        db.add_all([user_message, assistant])
        db.flush()
        db.add(
            MessageEvidence(message_id=assistant.id, sources=selected, ai_mode=mode())
        )
        session.updated_at = now
        response = ChatResponse(
            answer=answer, session_id=session.id, sources=selected, ai_mode=mode()
        ).model_dump()
        db.add(
            ChatTurn(
                request_id=str(payload.request_id),
                user_id=user_id,
                session_id=session.id,
                question=question,
                response=response,
            )
        )
        db.commit()
        return response
    finally:
        chat_slot.release()


@router.get("/chat/sessions")
def sessions(
    workspace_id: str | None = None,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    query = db.query(ChatSession).filter_by(user_id=user_id)
    if workspace_id:
        owned_workspace(db, workspace_id, user_id)
        query = query.join(
            SessionWorkspace, ChatSession.id == SessionWorkspace.session_id
        ).filter(SessionWorkspace.workspace_id == workspace_id)
    rows = (
        query.order_by(ChatSession.updated_at.desc(), ChatSession.id).limit(100).all()
    )
    return [
        {
            "id": row.id,
            "title": row.title,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "workspace_id": binding.workspace_id
            if (binding := db.get(SessionWorkspace, row.id))
            else None,
        }
        for row in rows
    ]


@router.get("/chat/sessions/{session_id}/messages")
def messages(
    session_id: str, user_id=Depends(get_current_user_id), db: Session = Depends(get_db)
):
    session = db.query(ChatSession).filter_by(id=session_id, user_id=user_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    # Return the most recent 200 in chronological order, with an explicit UI limit.
    rows = (
        db.query(Message)
        .filter_by(session_id=session_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at,
            "sources": evidence.sources
            if (evidence := db.get(MessageEvidence, row.id))
            else [],
            "ai_mode": evidence.ai_mode if evidence else "legacy",
        }
        for row in reversed(rows)
    ]
