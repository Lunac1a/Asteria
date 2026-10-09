from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class Observation(BaseModel):
    kind: Literal["attempt", "question", "correction", "reflection"]
    text: str = Field(min_length=1, max_length=1000)
    quote: str = Field(min_length=1, max_length=2000)
    message_id: str


class NotesEdit(BaseModel):
    revision: int = Field(ge=1)
    goal: str = Field(min_length=1, max_length=4000)
    focus: str = Field(max_length=1000)
    notes: str = Field(max_length=12000)
    next_step: str = Field(max_length=1000)
    evidence: list[Observation] = Field(max_length=1000)


class RecapContent(BaseModel):
    explored: str = Field(max_length=4000)
    tried: str = Field(max_length=4000)
    unclear: str = Field(max_length=4000)
    next: str = Field(max_length=4000)


class GenerateRecap(BaseModel):
    ui_locale: Literal["zh-CN", "en"] | None = None
    request_id: UUID
    revision: int = Field(ge=1)
    target_recap: str | None = None


class Finish(BaseModel):
    revision: int = Field(ge=1)
    cycle: int = Field(ge=1)
    draft_id: str | None = None
    content: RecapContent | None = None
    without_recap: bool = False


class Revision(BaseModel):
    revision: int = Field(ge=1)


class RecapEdit(Revision):
    content: RecapContent
