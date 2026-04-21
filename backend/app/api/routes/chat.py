from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.core.deps import get_current_user_id
from app.services.nvidia_nim_api_service import generate_response

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
def chat(
        payload: ChatRequest,
        user_id: str = Depends(get_current_user_id)
):
    answer = generate_response(payload.message)
    return ChatResponse(answer=answer)