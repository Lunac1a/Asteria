from fastapi import APIRouter
from app.schemas.chat import ChatRequest, ChatResponse
from app.api.deps import get_current_user_id

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    user_message = payload.message

    return ChatResponse(
        answer=f"Backend received: {user_message}"
    )