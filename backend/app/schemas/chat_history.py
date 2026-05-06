from pydantic import BaseModel
from datetime import datetime

class ChatSessionResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime