---
name: paic-experiment
description: Design an experiment plan (research questions / baselines / proposed method / metrics / ablations / threats to validity) for an existing IdeaCard. Use after /paic-ideate when the user wants to flesh out a candidate.
allowed-tools: mcp__paic__paic_experiment_start, mcp__paic__paic_workspace_status
---

# /paic-experiment — design an experiment plan

## When to use
- After `/paic-ideate` produced one or more `idea_id`s, the user picks one and says "为这条设计实验" / "give me an experiment plan for X".

## What you do

1. Confirm the target `idea_id` in 1 short Chinese sentence.
2. If the user mentioned constraints ("只用一张 A6000"、"两周内做完"、"不要用 ImageNet"），collect them as a `constraints` dict.
3. Call `mcp__paic__paic_experiment_start(project_dir=<cwd>, idea_id=<id>, constraints={...})`.
   - This is **synchronous** and runs to completion in one shot (no interrupts in MVP).
   - Watch for `error: idea_not_found` → tell user to verify the id with `/paic-ideate` history.
   - Watch for `error: llm_unavailable` → tell user to set `ANTHROPIC_API_KEY`.
4. Read the produced YAML (`experiment_path` field in the response) and render a Chinese summary:
   - **研究问题** / **假设**
   - **数据集** + 选择理由
   - **基线方法** + 为什么选它们
   - **核心方法**（保留英文段落原文）
   - **评估指标**（标注 primary）
   - **消融实验轴**
   - **算力预算 / 时间表**
   - **成功判据**
   - **威胁** （threats to validity）
5. End with: "下一步：`/paic-review <experiment_id>` 让 4 位评审 agent 审稿。"

## Style
- 中文叙述，英文术语保留（"baseline"、"ablation"、"primary metric" 等）。
- `proposed_method` 段保留原英文，不要尝试翻译伪代码或公式。

## 已知陷阱

- **splits 字段**：LLM 有时会为 `datasets.*.splits` 的值输出描述性字符串（如 "7 subjects training split"）而非整数。PAI-C schema 会自动提取字符串中的第一个整数作为 sample count；若无法提取则忽略该 key。如果 splits 对结果影响较大，可在 `constraints` 里加说明（如 `"splits": "train 50000 test 10000"`）让 LLM 输出精确数字。
- **`error: graph_failed` 兜底流程**：除 splits 外的字段没有 coerce validator，LLM 偶尔会输出破坏 `_DesignFields` schema 的内容。看到 `graph_failed` 时按以下顺序处理：
  1. 看返回的 `detail` 字段，里面是 `repr(exc)`，能定位是哪个字段类型不对
  2. 参照 `src/paic/schemas/experiment.py` 的 `ExperimentPlan` 类手写一份 YAML 落到 `<project>/.paic/experiments/<exp_id>.yaml`（id 自取，比如 `exp_manual_<idea_id>`）
  3. 直接对该 `experiment_id` 跑 `/paic-review`——`/paic-review` 入口会做 `ExperimentPlan.model_validate` 校验，校验过就能正常 review；不过则返回 `error: experiment_schema_invalid` 告诉你哪个字段错
- **schema 位置**：实验 YAML 字段对应 `ExperimentPlan` —— **必填** `id` / `idea_id` / `proposed_method` / `created_at`；其余可空列表/None。list 字段：`research_questions` / `hypotheses` / `datasets` / `baselines` / `metrics` / `ablations` / `success_criteria` / `threats_to_validity`。
