"""DraftSection schema."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DraftStatus = Literal["empty", "filled", "polished", "composed", "frozen"]


class DraftSection(BaseModel):
    id: str
    section_name: str
    template_id: str | None = None
    status: DraftStatus = "empty"
    text: str = ""
    referenced_papers: list[str] = Field(default_factory=list)
    referenced_ideas: list[str] = Field(default_factory=list)
    referenced_experiments: list[str] = Field(default_factory=list)
    last_edit_run_id: str | None = None
    word_count: int = 0
    updated_at: datetime
