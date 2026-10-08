import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.core.security import encrypt_text
from app.schemas.llm_settings import (
    LLMSettingsCreate,
    LLMSettingsResponse,
)
from app.models.user_llm_settings import UserLLMSetting

router = APIRouter()


@router.post("/settings/llm", response_model=LLMSettingsResponse)
def upsert_llm_settings(
    payload: LLMSettingsCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    setting = db.query(UserLLMSetting).filter_by(user_id=user_id).first()

    if setting:
        if payload.api_key:
            setting.encrypted_api_key = encrypt_text(payload.api_key)
        setting.model_name = payload.model_name
        setting.base_url = payload.base_url
    else:
        if not payload.api_key:
            raise HTTPException(status_code=400, detail="API key required for initial setup")
        setting = UserLLMSetting(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider="nvidia",
            encrypted_api_key=encrypt_text(payload.api_key),
            model_name=payload.model_name,
            base_url=payload.base_url,
        )
        db.add(setting)

    db.commit()
    db.refresh(setting)

    return LLMSettingsResponse(
        provider=setting.provider,
        model_name=setting.model_name,
        base_url=setting.base_url,
        has_api_key=True,
    )


@router.get("/settings/llm", response_model=LLMSettingsResponse)
def get_llm_settings(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    setting = db.query(UserLLMSetting).filter_by(user_id=user_id).first()

    if setting is None:
        raise HTTPException(status_code=404, detail="Settings not found")

    return LLMSettingsResponse(
        provider=setting.provider,
        model_name=setting.model_name,
        base_url=setting.base_url,
        has_api_key=bool(setting.encrypted_api_key),
    )
