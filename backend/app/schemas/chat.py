from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID, uuid4

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None
    workspace_id: str | None = None
    request_id: UUID = Field(default_factory=uuid4)

class ChatResponse(BaseModel):
    answer: str
    session_id: str
    sources: list[dict] = Field(default_factory=list)
    ai_mode: str = "provider"
