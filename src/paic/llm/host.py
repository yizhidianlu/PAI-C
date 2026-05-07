"""Host orchestration directive — strongly-typed payload for nodes routed to ``host``.

When a routing node resolves to the ``host`` sentinel backend (see
:mod:`paic.llm.router`), PAI-C does NOT call any LLM itself. Instead the
MCP tool returns a :class:`HostOrchestrationDirective` describing what the
main Claude Code conversation should generate, and the Skill layer hands
the result back via a ``*_persist`` MCP tool (synchronous nodes) or a
``*_step`` / ``*_resume`` call carrying ``host_response`` (graph nodes).

Wire format
-----------
``to_dict()`` flattens ``metadata`` to top level so the resulting dict is a
back-compatible superset of the ad-hoc dicts that ``summarize`` /
``draft_polish`` / ``draft_compose`` returned before this module existed.
New nodes should populate ``metadata`` for tool-specific fields rather
than subclassing the model.

In-graph nodes
--------------
``llm_or_interrupt`` is the Phase C primitive: a graph node calls it
exactly the way it would call ``llm.complete_json``, and either gets a
validated Pydantic instance (cloud mode) or pauses the graph via
``langgraph.types.interrupt(...)`` and resumes with a host-supplied
payload (host mode). The pause / resume is transparent to graph code —
state field additions are not required because LangGraph's checkpointer
already persists interrupt payloads.
"""

from __future__ import annotations

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

HOST_MODE = "host_orchestration"

_T = TypeVar("_T", bound=BaseModel)


class HostOrchestrationDirective(BaseModel):
    """Payload returned (or interrupt-emitted) when a node is host-routed."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["host_orchestration"] = HOST_MODE
    node: str
    instructions: str
    user_prompt: str | None = None
    schema_hint: dict[str, Any] | None = None
    next_tool: str | None = None
    resume_token: str | None = None
    original_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize as a flat dict, with ``metadata`` keys promoted to top level.

        Raises:
            ValueError: if a metadata key collides with a top-level field
                name. Silent shadowing would let a tool-specific extra
                quietly overwrite a typed core field — surface it instead.
        """
        d = self.model_dump(mode="json", exclude_none=True)
        meta = d.pop("metadata", {}) or {}
        collisions = sorted(set(meta) & set(d))
        if collisions:
            raise ValueError(
                f"host directive metadata shadows top-level field(s): {collisions}"
            )
        d.update(meta)
        return d


def build_host_directive(
    *,
    node: str,
    instructions: str,
    user_prompt: str | None = None,
    schema_hint: dict[str, Any] | None = None,
    next_tool: str | None = None,
    resume_token: str | None = None,
    original_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> HostOrchestrationDirective:
    """Construct a directive without spelling out the model class.

    Keyword-only on purpose: positional args would invite silently
    swapping ``instructions`` and ``user_prompt`` at call sites, both of
    which are strings.
    """
    return HostOrchestrationDirective(
        node=node,
        instructions=instructions,
        user_prompt=user_prompt,
        schema_hint=schema_hint,
        next_tool=next_tool,
        resume_token=resume_token,
        original_hash=original_hash,
        metadata=dict(metadata or {}),
    )


# --------------------------------------------------------------------- in-graph
# Phase C primitive — drop-in replacement for ``deps.llm.complete_json`` used
# inside LangGraph nodes. When the node's routing label resolves to ``host``,
# the call interrupts the graph with a directive payload; otherwise it falls
# through to the regular cloud-LLM path.

_DEFAULT_HOST_INSTRUCTIONS = (
    "Take the role described in `metadata.system_prompt`, process "
    "`user_prompt`, and return a single JSON object that matches "
    "`schema_hint`. Then call `next_tool` with `run_id` and "
    "`host_response=<your JSON>` to resume the run."
)


def llm_or_interrupt(
    deps: Any,
    *,
    node: str,
    system: str,
    user: str,
    schema: type[_T],
    run_id: str | None = None,
    resume_tool: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    **complete_kwargs: Any,
) -> _T:
    """LLM call that transparently pauses on ``host`` routing.

    Parameters mirror ``LLMClient.complete_json`` plus three host-specific
    kwargs:

    - ``run_id``: the LangGraph thread/run id, surfaced as ``resume_token``
      so the Skill knows which run to resume.
    - ``resume_tool``: fully-qualified MCP tool name (e.g.
      ``"mcp__paic__paic_review_step"``) the Skill should call with the
      host's response.
    - ``extra_metadata``: tool-specific extras packed into
      ``directive.metadata`` for the Skill (e.g. persona name on review
      nodes, scoring persona on ideate).

    ``deps`` is duck-typed: any object with ``router`` (LLMRouter) and
    ``llm`` (LLMClient) attributes works. ``router`` may be ``None`` —
    that's the legacy code path and just goes straight to ``llm``.
    """
    from langgraph.types import interrupt  # local import: keeps non-graph callers free of langgraph dep

    router = getattr(deps, "router", None)
    if router is not None and router.is_host_orchestrated(node):
        directive = build_host_directive(
            node=node,
            instructions=_DEFAULT_HOST_INSTRUCTIONS,
            user_prompt=user,
            schema_hint=schema.model_json_schema(),
            next_tool=resume_tool,
            resume_token=run_id,
            metadata={
                "system_prompt": system,
                **(extra_metadata or {}),
            },
        )
        raw = interrupt({"stage": "host_orchestration", "directive": directive.to_dict()})
        return schema.model_validate(raw)

    return deps.llm.complete_json(
        system=system,
        user=user,
        schema=schema,
        node=node,
        **complete_kwargs,
    )
