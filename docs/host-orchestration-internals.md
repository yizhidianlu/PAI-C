# Host orchestration internals

> **Internal** · 开发者视角 —— directive schema / sync vs in-graph flow / 实现新 host-aware 节点。
> 用户配置（何时用 / yaml 模板 / 决策树）见 [configuration-cookbook.md § Host orchestration 配置](configuration-cookbook.md#host-orchestration-配置)；节点白名单见 [configuration.md § 节点路由表](configuration.md#节点路由表) 「host-aware」列。

## Coverage

21 of the 25 LLM-call nodes are host-aware. Routing them to `host`
(`routing.default: host` or `routing.overrides.<node>: host`) returns a
directive instead of calling the cloud LLM. The mechanism comes in two
flavours:

- **Synchronous MCP-tool nodes** return the host directive directly from the
  tool call. The Skill calls a companion `*_persist` tool to save the host's
  output. Examples: `summarize`, `paper_plan_generate`, `claim_extract`,
  `draft_polish`, `draft_compose`, `figure_plan`, etc.
- **In-graph nodes** (review / ideate / experiment graphs) pause via LangGraph's
  `interrupt(...)` and resume when the Skill calls `*_step` / `*_resume` with a
  `host_response={...}` payload. The graph's checkpoint persists the directive
  so resumes are durable across crashes.

The 4 cloud-only nodes (`claim_judge` / `paragraph_outline` / `paragraph_write`
/ `section_coherence_polish`) skip the `is_host_orchestrated` check and call
`llm.complete*` directly — routing them to `host` raises
`HostOrchestrationRequired` at first invocation. `paic doctor` rejects this
configuration at startup via `RoutingConfig.invalid_host_overrides` against the
`HOST_SUPPORTED_NODES` whitelist in `src/paic/config.py`.

## Directive schema

Every host-routed call returns a `HostOrchestrationDirective` (see
`src/paic/llm/host.py`). The wire format is a flat dict:

| Field            | Type            | Purpose                                                                                              |
|------------------|-----------------|------------------------------------------------------------------------------------------------------|
| `mode`           | `"host_orchestration"` | Sentinel — Skill branches on this.                                                            |
| `node`           | `str`           | Routing label (e.g. `"summarize"`, `"review_persona_methodology"`).                                  |
| `instructions`   | `str`           | What the host should do.                                                                             |
| `user_prompt`    | `str \| None`   | Rendered user message (in-graph nodes always set this; some sync nodes pack the body in `metadata`). |
| `schema_hint`    | `dict \| None`  | JSON Schema (`pydantic.model_json_schema()`) the host's output must satisfy.                         |
| `next_tool`      | `str \| None`   | Fully-qualified MCP tool name to call with the result (`mcp__paic__paic_*`).                         |
| `resume_token`   | `str \| None`   | LangGraph `run_id` for in-graph resumes (sync nodes leave this `null`).                              |
| `original_hash`  | `str \| None`   | Idempotency token for round-tripping mutable content (e.g. polished section).                        |
| *(metadata flat)*| various         | Tool-specific extras flattened to top level — `paper_id`, `cite_key`, `section`, persona, etc.       |

The flat layout is a back-compat superset of the ad-hoc dicts that
`summarize` / `draft_polish` / `draft_compose` returned before this module
existed; old Skills keep working unchanged.

## Sync MCP-tool flow

```
User → /paic-summarize → SKILL → paic_summarize_run
                                  → returns directive (mode=host_orchestration)
                          ← host LLM produces JSON matching schema_hint
                            paic_summarize_persist(structured=<JSON>)
                                  → writes summary on disk
```

Each sync host-aware tool ships with a `paic_<name>_persist` companion that
validates the host's JSON against the same Pydantic schema and runs the same
tail (writing files / merging ledgers) as the cloud-LLM path.

## In-graph flow (review / ideate / experiment)

```
paic_review_start → graph runs until first host-routed node →
  interrupt({"stage": "host_orchestration", "directive": {...}})
  → returns awaiting="host_orchestration", host_directive=<...>
SKILL: host LLM produces JSON
  paic_review_step(host_response=<JSON>)
  → graph resumes, runs to next interrupt or end
  → may pause again on another host node (e.g. next persona) or on
    "stage": "await_user" (the existing user-decision interrupt)
... loop until status="done"
```

Status / step responses now include:

- `awaiting`: `"host_orchestration"` | `"user"` | `null`
- `host_directive`: full directive dict (only when `awaiting == "host_orchestration"`)
- `interrupt_payload`: the raw payload (kept for back-compat — Skills written
  before this rewrite still parse it the old way)

## Failure modes

- **Bad JSON from host** → `*_step` returns `{"error": "host_response_invalid",
  "detail": [...pydantic errors...]}`. The Skill should fix the JSON and call
  `*_step(host_response=...)` again.
- **Skill cancels mid-flight** → call `paic_runs_cancel(run_id)`. The graph's
  checkpoint is retained for forensic reading but the run is marked
  `cancelled`.
- **`routing.fallback` interaction** → fallback is **never** substituted for a
  host-routed node. `host` is a deliberate redirect, not a transient failure.
  The router rejects `fallback: host` outright (would create silent re-routes).

## Latency notes

- `review_graph` is naturally serial — the persona queue pops one persona at a
  time. A fully host-routed review of 4 personas × N rounds + moderator +
  verdict therefore costs `4N + 2` Skill round-trips.
- `ideate_graph._score_panel` is parallel under cloud routing
  (ThreadPoolExecutor). When **any** `idea_score_*` persona resolves to `host`,
  the panel is forced serial (`interrupt()` cannot fire from a worker thread).
  Mixed cloud/host routing degrades to single-threaded scoring; if latency
  matters, route the whole panel uniformly.
- `experiment_graph` adds one host pause when `experiment_design` is routed to
  host. `paic_experiment_resume(run_id, host_response=...)` carries the
  generated plan back into the graph for `_verify_plan` and persistence.

## Implementing a new host-aware node

For a sync MCP tool, follow the `summarize` pattern:

```python
from paic.llm.host import build_host_directive
from paic.llm.router import LLMRouter

router = LLMRouter(load_config())
if router.is_host_orchestrated("my_node"):
    return build_host_directive(
        node="my_node",
        instructions="What the host should do, ending in 'call mcp__paic__paic_my_persist'.",
        user_prompt=rendered_user_msg,
        schema_hint=MyOutputSchema.model_json_schema(),
        next_tool="mcp__paic__paic_my_persist",
        metadata={...tool-specific extras...},
    ).to_dict()
```

For an in-graph node, swap `deps.llm.complete_json(...)` for `llm_or_interrupt`:

```python
from paic.llm.host import llm_or_interrupt

output = llm_or_interrupt(
    deps,
    node="my_graph_node",
    system=load_prompt("my_graph_node"),
    user=user_msg,
    schema=MyOutputSchema,
    run_id=state.get("run_id"),
    resume_tool="mcp__paic__paic_<graph>_step",  # or _resume
    extra_metadata={"persona": persona, ...},
)
```

The helper resolves to `deps.llm.complete_json(...)` when `deps.router` is None
or the node isn't host-routed, so the same code path serves both cloud and
host routing.

## See also

- `src/paic/llm/host.py` — directive model + `llm_or_interrupt` helper.
- `src/paic/llm/router.py` — `LLMRouter`, `HOST_BACKEND` sentinel, fallback rules.
- `docs/config.yaml.example` — full routing node list.
- `tests/test_host_phase_c.py` — integration tests covering review / ideate /
  experiment host flows.
