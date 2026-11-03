from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class CreateJob(BaseModel):
    objective: str = Field(min_length=12, max_length=2000)

class ClarifyRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)
    answers: list[str] = Field(default_factory=list, max_length=8)

class SourceSelection(BaseModel):
    url: str = Field(min_length=8, max_length=2000)

class JobView(BaseModel):
    id: str
    status: Literal["queued", "running", "awaiting_columns", "awaiting_review", "completed", "failed"]
    objective: str
    created_at: datetime
    updated_at: datetime
    events: list[dict[str, Any]] = []
    proposal: dict[str, Any] | None = None
    error: str | None = None

class ReviewRequest(BaseModel):
    approved: bool

class ColumnSelection(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=200)

