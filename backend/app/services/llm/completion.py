"""Invoke the configured user's model with bounded structured generation."""

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from app.core.config import settings
from app.core.security import decrypt_text
from app.models.user_llm_settings import UserLLMSetting
from app.schemas.llm_settings import validate_provider_url
from app.services.llm.provider import generate_response
from app.services.llm.protocol import GenerationError, failure, response_format


def provider_completion(
    db,
    user_id,
    system,
    context,
    schema=None,
    timeout_seconds=None,
    metric_phase="generation",
):
    from app.services.runtime_metrics import provider_phase

    with provider_phase(metric_phase):
        return _provider_completion(
            db, user_id, system, context, schema, timeout_seconds
        )


def _provider_completion(
    db, user_id, system, context, schema=None, timeout_seconds=None
):
    setting = db.query(UserLLMSetting).filter_by(user_id=user_id).first()
    if not setting:
        raise HTTPException(400, "Configure your LLM provider in Settings first")
    try:
        url = validate_provider_url(setting.base_url)
        key = decrypt_text(setting.encrypted_api_key)
    except (InvalidToken, ValueError):
        raise HTTPException(
            400, "LLM settings are invalid. Please save your provider settings again."
        )
    model_name = setting.model_name
    db.rollback()
    try:
        return generate_response(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
            key,
            url,
            model_name,
            **(
                {"timeout_seconds": timeout_seconds}
                if timeout_seconds is not None
                else {}
            ),
            **(
                {"response_format": fmt}
                if (
                    fmt := response_format(
                        url,
                        model_name,
                        schema,
                        settings.GENERATION_JSON_SCHEMA_PROFILES,
                    )
                )
                else {}
            ),
        )
    except GenerationError as exc:
        raise failure(exc) from None
    except RuntimeError as exc:
        if "time limit" in str(exc) or "timeout" in str(exc).lower():
            raise HTTPException(
                504, "The model timed out. Your question was not saved; retry shortly."
            )
        raise HTTPException(
            502,
            "The model provider is unavailable. Check Settings and retry; this turn was not saved.",
        )
