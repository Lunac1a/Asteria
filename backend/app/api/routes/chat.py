from fastapi import APIRouter
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    user_message = payload.message

    return ChatResponse(
        answer=f"Backend received: {user_message}"
    )