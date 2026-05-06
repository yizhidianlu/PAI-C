---
name: paic-resume
description: List or resume LangGraph runs that were paused (e.g. waiting on user filtering, multi-agent review checkpoints). Use when the user opens a fresh Claude Code session and asks "上次到哪了" or "继续上次的".
allowed-tools: mcp__paic__paic_runs_list, mcp__paic__paic_runs_resume, mcp__paic__paic_runs_cancel
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

## Style
- 中文。
- 列表用紧凑表格，run_id 截短到前 8 位（完整值在 hover 提示里也行）。
- 不要主动恢复——必须用户给出 run_id 才行动。
