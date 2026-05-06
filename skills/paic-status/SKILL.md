---
name: paic-status
description: Print a one-page overview of the current PAI-C project — paper count, idea count, experiment count, review count, and any in-flight LangGraph runs. Use whenever the user asks "where are we", "what's the status", or before resuming work.
allowed-tools: mcp__paic__paic_workspace_status
---

# /paic-status — project overview

## When to use
- User runs `/paic-status` or asks "现在进度如何？" / "where are we?".
- Before `/paic-resume` to remind the user of which run to resume.

## What you do
1. Call `mcp__paic__paic_workspace_status` (no argument; let it auto-detect from cwd).
2. If `initialized: false`, tell the user (中文) to run `/paic-init` first and stop.
3. Otherwise render a compact Chinese summary:
   - 项目: `<title>` @ `<venue>`，截止 `<deadline>`
   - 文献库: N 篇
   - 创新点: N 条
   - 实验方案: N 份
   - 评审: N 项
   - 进行中的 run: 列出 run_id / kind / current_node / updated_at（若为空说一句"无"）

## Style
- 中文输出。一屏可读完。
- 不要追加任何"接下来该做什么"的建议，除非用户问。
