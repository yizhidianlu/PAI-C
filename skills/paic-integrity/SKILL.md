---
name: paic-integrity
description: Run the paper-truthfulness integrity gate (5-type citation hallucination + 7-mode AI failure checklist). Use at Stage 6 (pre-review) and Stage 9 (final-check) of the pipeline. Triggers on "integrity check", "反引文幻觉", "100% verify cite", "AI failure mode check", "pre-submission integrity".
allowed-tools: mcp__paic__paic_integrity_check, mcp__paic__paic_integrity_persist, WebSearch
---

# /paic-integrity — paper-truthfulness gate (ARS-fusion P0-1)

## When to use

- **Stage 6 INTEGRITY-PRE** (`mode=pre_review`) — before peer review, after `/paic-draft compose` and `/paic-finalize` (structural gate). Runs 5-type citation hallucination check + 7-mode AI failure checklist.
- **Stage 9 INTEGRITY-FINAL** (`mode=final_check`, `from_scratch=True`) — after revisions, before submission. Re-verifies every citation independently (cache invalidated). MUST PASS to advance to Stage 10 FINALIZE.
- **Standalone** — `/paic-integrity` whenever the user wants a quick truthfulness audit on the current draft + library.

## Cost & latency awareness

⚠️ **First-call cost depends on library size**:

- ~70% of citations resolve via S2 batch (free, no API key needed beyond what `/paic-search` already uses)
- ~30% route to WebSearch (host orchestration → main conversation runs Claude's built-in WebSearch). For 100 refs this is ~30 WebSearch round-trips (~3-10 minutes wall-clock)
- 6 AI failure mode prompts (mode 2-7; M1 delegated to citation check) — each is one LLM judge call

**Show this notice before the first call**:

```
即将运行 /paic-integrity（mode=<pre_review|final_check>）：
  · S2 batch 验证 ~N 篇 cite (~5s)
  · 失败 cite 走 WebSearch 验证（每条 ~30s）
  · 6 个 AI 失败模式 LLM judge（每条 ~10s + cache）
预估总时长 5-15 分钟，token 成本视库大小而定。继续？
```

Wait for user confirm before calling `mcp__paic__paic_integrity_check`.

## Flow

### Step 1 — first call

```
mcp__paic__paic_integrity_check(
    project_dir=<cwd>,
    mode="pre_review",          # or "final_check"
    from_scratch=False,         # True at Stage 9 to re-verify cache
    mandatory_modes=[1, 3, 5, 6],  # default; pass [] for fully advisory
    s2_enabled=True,            # set False to skip S2 (everything → WebSearch)
)
```

**Two response shapes** depending on `routing.overrides.integrity_judge`:

#### A. `mode: "host_orchestration"` (default, recommended)

The directive bundles two parallel tasks:

1. **WebSearch verification** — `metadata.pending_websearch` lists each citation S2 could not confirm. For each entry:
   - Query Claude's built-in `WebSearch` with `<title> <first author> <year>`
   - If no relevant result after 3 different queries → `verdict: NOT_FOUND`
   - If found exact match (DOI / publisher page / Google Scholar) → `verdict: VERIFIED` with `evidence_url`
   - If found a related but different paper → `verdict: MISMATCH` with corrected `matched_title / matched_authors / matched_year / matched_doi`

2. **AI failure mode judgment** — `metadata.pending_ai_judge` lists each mode 2-7 prompt with pre-loaded paper artefacts. For each:
   - Take the role described in `metadata.pending_ai_judge[*].label`
   - Read the `inputs` block (claims / experiments / paper plan slice)
   - Emit `{mode, status, reasoning, evidence}` per the schema
   - Use `INSUFFICIENT_EVIDENCE` freely when the inputs don't carry the signal a mode needs (theory / position papers should NOT trip M3)

Then call:

```
mcp__paic__paic_integrity_persist(
    project_dir=<cwd>,
    mode=<integrity_mode from metadata>,
    mandatory_modes=<metadata.mandatory_modes>,
    structural_issues=<metadata.structural_issues>,  # PASS THROUGH
    web_search_results=[ <your verdicts> ],
    ai_judge_results=[ <your judgments> ],
)
```

> Note: the directive surfaces the integrity-mode tag as `integrity_mode`
> (not `mode`) because the directive itself uses `mode: "host_orchestration"`
> at the top level. Same reason `notes` is surfaced as `integrity_notes`.

#### B. Direct response (`mode` not in routing → cloud / fixed backend)

Returns a partial `IntegrityResult.to_dict()` with the AI judge run inline. Citations still need WebSearch — call `paic_integrity_persist` with at least `web_search_results` populated.

### Step 2 — render the report

After `paic_integrity_persist` returns the final report, render to the user:

```
🛡  Integrity gate (mode=<mode>)
   · passed=<true|false>  · S2 verified <N>  · failed <M>  · cache hits <K>

Issues by severity:
  blocker (N)
    [TF]  cite_key=<key> — <detail>
    [AI_FAIL_M3] <detail>
  major (N)
    [PH]  cite_key=<key> — <detail>
    ...
  minor (N)
    [SH]  cite_key=<key> — year drift
    ...

Notes:
  · AI_FAIL_M3 skipped — paper_kind=theoretical → no numeric results to verify
  · ...
```

For each TF issue, ask: 删除此 cite / 替换 / override? For each MISMATCH, show the `suggested_correction` and ask whether to apply.

### Step 3 — handle blockers

Per ARS iron rule (and PAI-C V1.0 design): **blocker-severity issues cannot be overridden**. Tell the user:

```
存在 N 个 blocker — Stage 6/9 闸门不可通过，必须先修：
  1. <issue 1 fix steps>
  2. ...
```

Offer to dispatch the fix (e.g. "remove fabricated cite from selected.yaml + drafts" or "add experiment_id to claim X").

## Errors

- `error: project_not_initialized` — `/paic-init` first
- `error: s2_unavailable` (silent in WebSearchPending.reason) — S2 hit rate-limit; pending list will be larger than usual, more WebSearch round-trips
- Pending list empty AND no issues → integrity gate clean, advance to next stage

## Style

- 中文叙述。Cite key / kind code (TF/PAC/IH/PH/SH/AI_FAIL_M*) / WebSearch URL 保留英文。
- 分 severity 渲染（blocker / major / minor）；blocker 用 ⚠️ 高亮。
- WebSearch 结果展示 evidence_url 让用户能 click verify。
- override 提示带语义警告：blocker override 不生效（V1.0 design）。

## Known traps

- **WebSearch round-trip latency**: 100+ ref 论文整 gate ~10-15 分钟。建议先跑 `/paic-finalize`（结构 gate，秒级），再跑 integrity（耗时 gate）。
- **theory / position paper 误报 M3**: 设置 `paper_plan.paper_kind: theoretical`，runner 会自动 skip M3。或在 `mandatory_modes=[1, 5, 6]` 把 M3 退化为 advisory。
- **DOI Misdirection**: 见过的 fabricated DOI 解析到不相关 paper。WebSearch 时务必校 title / authors / year 三联，不要只看 DOI 是否能解析。
- **S2 cache 持续 1 周**: `from_scratch=True` 不影响 S2 自身的磁盘缓存（在 `~/.paic/cache/s2/`）；它只清 PAI-C 的 integrity_cache。
