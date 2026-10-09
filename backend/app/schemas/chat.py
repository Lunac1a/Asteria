from pydantic import BaseModel, Field
from typing import Optional, Literal
from uuid import UUID, uuid4


class ChatRequest(BaseModel):
    ui_locale: Literal["zh-CN", "en"] | None = None
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None
    workspace_id: str | None = None
    session_type: Literal["questioning", "learning"] | None = None
    answer_mode: Literal["smart", "document"] = "smart"
    learning_mode: Literal["direct", "socratic"] = "direct"
    request_id: UUID = Field(default_factory=uuid4)


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    session_type: Literal["questioning", "learning"] | None = None
    learning_goal: str | None = None
    sources: list[dict] = Field(default_factory=list)
    ai_mode: str = "provider"

    answer_basis: str = "grounded"
    answer_mode: str = "smart"
    learning_mode: str = "direct"
