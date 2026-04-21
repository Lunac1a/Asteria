from fastapi import APIRouter, Depends
from app.schemas.chat import ChatRequest, ChatResponse
from app.api.deps import get_current_user_id

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
def chat(
        payload: ChatRequest,
        user_id: str = Depends(get_current_user_id)
):
    user_message = payload.message

    return ChatResponse(
        answer=f"[user:{user_id}] {user_message}"
    )