---
name: paic-review
description: Run a multi-agent (4-persona) review on an experiment plan. Methodology / statistics / domain / Reviewer-2 each critique; a moderator agent synthesizes; the author can rebut between rounds. Use after /paic-experiment when the user wants to stress-test the plan.
allowed-tools: mcp__paic__paic_review_start, mcp__paic__paic_review_step, mcp__paic__paic_review_status, mcp__paic__paic_revision_extract, mcp__paic__paic_revision_list, mcp__paic__paic_revision_apply, mcp__paic__paic_revision_resolve
---

# /paic-review — multi-agent experiment review

## Flow

This is a **multi-step** skill. Each round, the graph runs all 4 personas + a moderator, then PAUSES at `await_user` for the author to respond. After ``rounds`` rounds (default 2), the graph emits a final verdict.

### Step 1 — start
1. Confirm the target `experiment_id` and the desired number of rounds (default 2) in 1 short Chinese sentence.
2. Call `mcp__paic__paic_review_start(project_dir=<cwd>, experiment_id=<id>, rounds=2)`.
   - The response contains `panel` with `panel_summary` + `issues` + `open_questions_for_author` from this round's moderator synthesis.
   - `status: "awaiting_input"` means it's pause-time.

### Step 2 — render the panel & ask author
1. Render the round in Chinese:
   - 顶部一行: "第 N 轮 / 共 M 轮，主持总结："
   - 然后 panel_summary 段落
   - 然后按 issues 排序列出: "#rank [severity] title — description (raised by: ...)"
   - 最后列 open_questions_for_author
2. 询问用户 (中文一句话): 怎么回应？可以选择
   - 给一段 rebuttal 文本 (回复每条 issue)
   - 提供修订后的 experiment YAML 全文 (作为 `plan_diff`)
   - 直接跳到 verdict (skip_to_verdict)

### Step 3 — submit user response
1. Call `mcp__paic__paic_review_step(project_dir=<cwd>, run_id=<from-step-1>, rebuttal=..., plan_diff=..., skip_to_verdict=...)`.
2. If `status: "awaiting_input"` again, loop back to Step 2 (next round).
3. If `status: "done"`, render the final `verdict`:
   - 决议: accept / minor_revision / major_revision / reject
   - 理由 (rationale)
   - 必改 (must_fix) 列表
   - 可改 (nice_to_fix) 列表
   - 完整 transcript 路径: `.paic/reviews/<experiment_id>/transcript.yaml`

### Step 4 — extract revision tasks (§quality phase 8)

每完成一轮 review（含 verdict 后的 transcript），把 panel synthesis + 各 persona critique 转成可追踪的 RevisionTask 队列：

```
mcp__paic__paic_revision_extract(
  project_dir=<cwd>,
  review_payload={"moderator": "<panel_summary>", "critiques": {<persona>: <critique text>}},
  round_num=<this round's index, 1-based>,
)
```

返回 `extracted_count` + `tasks` 列表（每条 `id` / `severity` / `target_kind` / `target_ref` / `summary` / `patch_hint`）。落到 `.paic/revisions/<round>_<id>.yaml`。

渲染中文 task 表格让用户挑哪些立刻处理：
```
| ID         | 严重 | 目标             | 概要              |
| t_xxxxx    | blocker | section/01_intro | 缺第 3 条 contribution 的 motivation |
```

用户处理后调 `paic_revision_resolve(project_dir, task_id, resolution_summary="改了 abstract 第 2 段")`。下次 `/paic-review` 仍在 `paic_revision_list(status="open")` 看遗留项。

### Step 5 — what next
- 如果 verdict 是 `minor_revision` 或 `accept`，提示用户 "可以运行 `/paic-draft fill --template <模板>` 起 LaTeX 骨架"。
- 如果是 `major_revision` 或 `reject`，建议 "考虑重新设计实验或回到 `/paic-ideate` 选另一条 idea"。

## Style
- 中文。issues 表格里的 title/description 保留 LLM 输出的英文。
- 不要替用户写 rebuttal——把决定权留给用户。
- 跨会话：用户中途关闭后，`/paic-resume <run_id>` 即可继续，本 skill 不需要单独处理。

## 错误情况
- `error: experiment_not_found` → 提示运行 `/paic-experiment <idea_id>` 先生成。
- `error: unknown_personas` → 用户传错了 persona name；显示有效集合 ["methodology","statistics","domain","reviewer2"]。
- `error: experiment_schema_invalid` → 用户/Claude 手写 YAML 时字段类型错了。`detail` 字段里有 pydantic 的 errors 列表，逐项指向出错字段。**不要硬扛**——直接照下面 schema 段把 YAML 改对再重跑。

## 实验 YAML schema（手写时参照）

`/paic-review` 入口会做 `ExperimentPlan.model_validate` 校验，**字段类型错就立即返回 `experiment_schema_invalid`**（不再静默崩成 graph_failed / AttributeError）。最常见手写翻车：

- `metrics` 必须是 list[dict]，**不能是单个 dict**：
  ```yaml
  # 错
  metrics: {name: accuracy, direction: max, primary: true}
  # 对
  metrics:
    - {name: accuracy, direction: max, primary: true}
  ```
- 这些字段全部是 list（哪怕只有一个元素也得套 `-`）：`datasets` / `baselines` / `ablations` / `success_criteria` / `threats_to_validity` / `research_questions` / `hypotheses`
- `datasets[].splits.train` / `splits.test` 必须是整数。PAI-C schema 会从字符串里抽数字（"7 subjects" → 7），但描述性字符串里没数字会被丢成 None
- 顶层 `id` / `idea_id` / `proposed_method` / `created_at` 必填；其它字段可省

完整字段定义见 `src/paic/schemas/experiment.py:ExperimentPlan`。手写最简骨架：

```yaml
id: exp_manual_<idea_id>
idea_id: <idea_id>
proposed_method: |
  One detailed paragraph describing the technical approach.
created_at: 2026-05-05T00:00:00Z
research_questions:
  - "Does X improve Y?"
hypotheses:
  - "X increases Y by ≥2 pp at p<0.05"
metrics:
  - {name: top1_accuracy, direction: max, primary: true}
status: draft
```
