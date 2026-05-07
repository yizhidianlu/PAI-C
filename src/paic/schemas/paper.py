"""Paper-related schemas: PaperRef, PaperSummary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PaperSource = Literal["arxiv", "s2", "external", "manual"]


class PaperRef(BaseModel):
    arxiv_id: str | None = None
    doi: str | None = None
    s2_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    pdf_local_path: str | None = None
    source: PaperSource = "manual"
    # §18: when source="external", ``platform`` carries the specific upstream
    # name (pubmed / biorxiv / openalex / …). For source="arxiv" or "s2" it is
    # informational and may be omitted. ``external_ids`` carries upstream-
    # specific identifiers that don't fit arxiv_id/doi/s2_id (PMID, PMCID,
    # OpenAlex W-id, …) — purely informational; dedupe still keys on
    # arxiv_id / doi / s2_id / title.
    platform: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_id(self) -> "PaperRef":
        if not (self.arxiv_id or self.doi or self.s2_id):
            if not self.title:
                raise ValueError("PaperRef requires arxiv_id, doi, s2_id, or title")
        return self

    def primary_id(self) -> str:
        return self.arxiv_id or self.doi or self.s2_id or self.title


class PaperSummary(BaseModel):
    paper: PaperRef
    problem: str
    method: str
    key_results: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    relevance_to_project: str | None = None
    # §quality phase 3 — structured evidence fields. All Optional so old
    # summary yamls (pre-phase-3, six-field shape) load without
    # validation errors.
    contribution_type: str | None = None
    """One of ``method`` / ``system`` / ``benchmark`` / ``survey`` /
    ``theory`` / ``application`` / ``empirical_study`` (free-form, but
    these are the common buckets)."""

    datasets: list[str] = Field(default_factory=list)
    """Dataset names referenced (e.g. ``"BCI-IV-2a"``, ``"ImageNet-1k"``)."""

    baselines: list[str] = Field(default_factory=list)
    """Named baseline methods compared against."""

    metrics: list[str] = Field(default_factory=list)
    """Evaluation metric names."""

    numeric_results: list[str] = Field(default_factory=list)
    """Free-form numeric claims, e.g. ``"+3.2% balanced accuracy on BCI-IV-2a"``."""

    assumptions: list[str] = Field(default_factory=list)
    """Stated or implicit assumptions the method depends on."""

    failure_modes: list[str] = Field(default_factory=list)
    """Conditions where the method underperforms or breaks down."""

    open_questions: list[str] = Field(default_factory=list)
    """Future-work items the paper itself identifies."""

    citation_claims: list[str] = Field(default_factory=list)
    """Claims this paper makes that other work would want to cite back at it."""

    quote_spans: list[str] = Field(default_factory=list)
    """Verbatim short quotes useful for direct citation. Keep ≤25 words each."""

    summarized_at: datetime
    summarizer_model: str
