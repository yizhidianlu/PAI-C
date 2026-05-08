"""Review-related schemas: Critique, Rebuttal, ReviewVerdict, ReviewTranscript."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PersonaName = Literal[
    "methodology",
    "statistics",
    "domain",
    "reviewer2",
    "devils_advocate",
]
Severity = Literal["blocker", "major", "minor", "nit"]
VerdictDecision = Literal["accept", "minor_revision", "major_revision", "reject"]


class Critique(BaseModel):
    persona: PersonaName
    round: int = Field(ge=1)
    severity: Severity
    category: str
    quote: str | None = None
    issue: str
    suggestion: str
    cited_papers: list[str] = Field(default_factory=list)
    created_at: datetime


class Rebuttal(BaseModel):
    persona: PersonaName | Literal["author"] = "author"
    round: int = Field(ge=1)
    response_to: list[int] = Field(default_factory=list)
    text: str
    plan_diff: str | None = None


class ReviewVerdict(BaseModel):
    decision: VerdictDecision
    rationale: str
    must_fix: list[str] = Field(default_factory=list)
    nice_to_fix: list[str] = Field(default_factory=list)


class ReviewTranscript(BaseModel):
    run_id: str
    experiment_id: str
    rounds_completed: int = 0
    critiques: list[Critique] = Field(default_factory=list)
    rebuttals: list[Rebuttal] = Field(default_factory=list)
    moderator_notes: list[str] = Field(default_factory=list)
    verdict: ReviewVerdict | None = None
    started_at: datetime
    ended_at: datetime | None = None
