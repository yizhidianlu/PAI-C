---
name: paic-resume
description: List or resume PAI-C state — LangGraph runs (paused at filter / review checkpoints) AND Material Passport boundaries (ARS-fusion P1-2 stage-level cross-session resume). Use when the user opens a fresh Claude Code session and asks "上次到哪了" / "继续上次的" / "passport=<hash>".
allowed-tools: mcp__paic__paic_runs_list, mcp__paic__paic_runs_resume, mcp__paic__paic_runs_cancel, mcp__paic__paic_passport_list, mcp__paic__paic_passport_resume
---

# /paic-resume — list/resume PAI-C runs

## Modes

### A. No argument — listing mode
1. Call `mcp__paic__paic_runs_list(project_dir=<cwd>)`. (Omit `project_dir` to see all projects.)
2. Render a Chinese table: run_id (前 8 位) / kind / status / current_node / updated_at / project (basename)
3. End with a hint: "运行 `/paic-resume <run_id>` 继续，或 `/paic-resume <run_id> cancel` 取消。"

### B. With `<run_id>` — resume mode
1. Lookup the run via `paic_runs_list` with `status_filter` left empty so cancelled/error runs also surface.
2. Branching:
   - `kind == "ideate"` and `status == "awaiting_input"`: ask the user which preview ideas to keep (re-fetch with the start tool's preview if needed — but normally the user has the table from before; if not, instruct them to re-list). Then call `mcp__paic__paic_runs_resume(run_id=..., keep=[...], feedback=...)`.
   - `kind == "experiment" | "review"`: route the resume call accordingly once those graphs land in Phase 6.
   - `status == "done" | "cancelled"`: tell the user this run is finished and stop.
   - `status == "error"`: show the error from the registry and ask whether to cancel.

### C. With `<run_id> cancel`
1. Confirm in 1 Chinese sentence.
2. Call `mcp__paic__paic_runs_cancel(run_id=...)`.

### D. `passport=<hash>` — Material Passport resume mode (ARS-fusion P1-2)

When 上一次会话在某个 FULL checkpoint emit 了 boundary（输出 `resume_command=resume_from_passport=<hash>`），本会话可以用同一个 hash 重新进入 pipeline。

1. （opt-in 检查）调 `mcp__paic__paic_passport_list(project_dir=<cwd>)`。`enabled: false` 说明 `~/.paic/config.yaml` 没开 passport，提示用户加 `passport.enable_reset_boundary: true`。
2. 调 `mcp__paic__paic_passport_resume(project_dir=<cwd>, hash=<hash>, chosen_branch=<可选>, stage_override=<可选>, mode_override=<可选>)`。
   - `pending_decision_required` → boundary 当时挂了多分支决议（如 review verdict accept/revise/reject），让用户从 `boundary.pending_decision.options[*].value` 里选一个传 `chosen_branch`。
   - `unknown_branch` → 用户选的分支不存在，重新列 options 让用户选。
   - `double_resume` → 这条 boundary 已经被某次 resume 消费过，不可重复（append-only ledger 规则）。
   - `hash_not_found` → 复制错了，让用户用 `paic_passport_list` 看 awaiting_resume 列表。
3. 渲染：「已恢复 stage=<recovered>，下一步=<next_stage>」+ instruction 字段。

> 注：passport resume 是**跨会话**节省 token 的机制（fresh session）；同一会话内继续不需要 passport，直接给指令即可。

## Listing — show both runs and passports

无参时：
1. `mcp__paic__paic_runs_list(project_dir=<cwd>)` 列 LangGraph runs
2. `mcp__paic__paic_passport_list(project_dir=<cwd>)` — 当 `enabled: true` 时另列 awaiting_resume boundaries
3. 引导用户选 run_id 或 passport hash

## Style
- 中文。
- 列表用紧凑表格，run_id 截短到前 8 位（完整值在 hover 提示里也行）。
- Passport hash 是 12 字符，全显示别截短（用户会复制）。
- 不要主动恢复——必须用户给出 run_id 或 hash 才行动。
