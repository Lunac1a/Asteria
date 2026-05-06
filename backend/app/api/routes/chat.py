import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import decrypt_text
from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.core.deps import get_current_user_id
from app.schemas.chat_history import ChatSessionResponse, MessageResponse
from app.services.nvidia_nim_api_service import generate_response

from app.models.user_llm_settings import UserLLMSetting
from app.models.chat_sessions import ChatSession
from app.models.messages import Message

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    setting = db.query(UserLLMSetting).filter_by(user_id=user_id).first()

    if setting is None:
        raise HTTPException(
            status_code=400,
            detail="LLM settings not configured"
        )

    try:
        api_key = decrypt_text(setting.encrypted_api_key)
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail="Failed to decrypt LLM API key",
        )

    if payload.session_id:
        session = (
            db.query(ChatSession)
            .filter_by(id=payload.session_id, user_id=user_id)
            .first()
        )

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Chat session not found",
            )
    else:
        session = ChatSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=payload.message[:40] or "New Chat",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    user_message = Message(
        id=str(uuid.uuid4()),
        session_id=session.id,
        role="user",
        content=payload.message
    )
    db.add(user_message)
    db.commit()

    try:
        answer = generate_response(
            message=payload.message,
            api_key=api_key,
            base_url=setting.base_url,
            model_name=setting.model_name,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    assistant_message = Message(
        id=str(uuid.uuid4()),
        session_id=session.id,
        role="assistant",
        content=answer
    )
    db.add(assistant_message)
    db.commit()

    return ChatResponse(
        answer=answer,
        session_id=session.id
    )

@router.get("/chat/sessions", response_model=list[ChatSessionResponse])
def get_sessions(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ChatSession)
        .filter_by(user_id=user_id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )

    return sessions

@router.get(
    "/chat/sessions/{session_id}/messages",
    response_model=list[MessageResponse],
)
def get_messages(
    session_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter_by(id=session_id, user_id=user_id)
        .first()
    )

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = (
        db.query(Message)
        .filter_by(session_id=session_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return messages