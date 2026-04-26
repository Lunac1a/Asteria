import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import decrypt_text
from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.core.deps import get_current_user_id
from app.services.nvidia_nim_api_service import generate_response
from app.models.user_llm_settings import UserLLMSetting

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

    answer = generate_response(
        message=payload.message,
        api_key=api_key,
        base_url=setting.base_url,
        model_name=setting.model_name,
    )

    return ChatResponse(answer=answer)