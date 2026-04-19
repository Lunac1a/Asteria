from datetime import datetime
from uuid import UUID
from pydantic import BaseModel

class UserRead(BaseModel):
    id: UUID
    email: str
    created_at: datetime

    class Config:
        from_attributes = True