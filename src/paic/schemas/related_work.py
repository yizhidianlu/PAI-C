"""RelatedWorkCluster — persisted at ``.paic/plans/related_work_clusters.yaml`` (§quality phase 7).

Related-work paragraphs read better when each one positions the proposed
method against a *cluster* of prior approaches (same dataset / same method
family / same limitation) rather than enumerating papers in retrieval
order. This schema captures that grouping decision so paragraph_compose
can produce one paragraph per cluster, each with an explicit contrast
point.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ClusterAxis = Literal["method", "dataset", "task", "limitation", "contribution_type"]


class RelatedWorkCluster(BaseModel):
    id: str
    """Stable id like ``RW1`` / ``RW2``."""

    label: str
    """Short noun-phrase label, e.g. "Channel-pruning approaches" or
    "Transformer-based EEG decoders"."""

    axis: ClusterAxis
    """The dimension this cluster is grouped on. Helps the paragraph
    writer pick the right contrast."""

    members: list[str] = Field(default_factory=list)
    """Cite_keys belonging to this cluster (subset of the project library)."""

    contrast_to_proposed: str
    """One- to two-sentence statement of how the proposed method differs
    from this cluster. Drives the related-work paragraph's contrast."""

    notes: str | None = None


class RelatedWorkPlan(BaseModel):
    """Top-level container for ``related_work_clusters.yaml``."""

    schema_version: int = 1
    clusters: list[RelatedWorkCluster] = Field(default_factory=list)
    last_updated_at: datetime | None = None

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> "RelatedWorkPlan":
        if not isinstance(data, dict):
            raise TypeError(
                f"RelatedWorkPlan.from_yaml_dict expects a dict, got {type(data).__name__}"
            )
        return cls.model_validate(data)
