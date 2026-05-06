---
name: paic-ideate
description: Generate research ideas grounded in the project's summarized literature. Multi-round refine flow with a 4-persona panel (methodology / novelty / impact / reviewer2) scoring each draft. User decides each round whether to finalize, regenerate (new batch with feedback), or refine (improve seeds + add fillers). Use after /paic-summarize when the user wants to brainstorm next directions.
allowed-tools: mcp__paic__paic_ideate_start, mcp__paic__paic_ideate_step, mcp__paic__paic_workspace_status
---

# /paic-ideate — generate research ideas

## Flow

iterative loop, **最多 max_rounds 轮**（默认 3）。每轮：brainstorm → 4-persona panel 评分 → 用户三选一 → 下一轮 / finalize。

### Step 1 — start

1. 一句中文确认 focus（可选；e.g. "long-context attention"）
2. 调 `mcp__paic__paic_ideate_start(project_dir=<cwd>, focus=<focus>, n_candidates=8, max_rounds=3)`
3. 等响应 — `status="awaiting_input"` + `drafts_with_scores` + `round=1` + `available_actions`

可选省成本提示（运行前一次即可）：
> "默认 4 persona panel evaluation。想省成本可改 `personas=['methodology','reviewer2']`（2 persona）；3 轮 cost 约砍一半。"

### Step 2 — render scored drafts + ask user

返回 `drafts_with_scores` 字段，每条含：
- `idx`, `title`, `one_liner`
- `feasibility / novelty / impact / composite`（avg across 4 personas）
- `panel_consensus`: `high_agreement` / `moderate` / `diverged` —— 一致度信号
- `red_flags`: 4 personas 提的具体红旗 union
- `persona_breakdown`: 每个 persona 的细分（feasibility/novelty/impact + rationale + red_flags）

**渲染中文表格**（核心列）：

```
| # | 标题 | 一句话 | 可行 | 新颖 | 影响 | 综合 | panel | 红旗 |
| 1 | Latent Diffusion for Tabular | 把 latent diffusion 用在小表格数据 | 0.65 | 0.55 | 0.45 | 0.57 | ✓一致 | (none) |
| 2 | Mixture-of-Experts for Code Review | ... | 0.45 | 0.80 | 0.60 | 0.61 | ⚠️分歧 | "no public dataset", "claim too broad" |
```

`panel` 列符号：
- `✓ 一致` (high_agreement)
- `⚠️ 中度分歧` (moderate)
- `🔴 严重分歧` (diverged) —— 提示用户**重点人审**这条

**红旗列**：截断到前 2-3 条最具体的，用户点开可看全。

### Step 3 — 询问用户三选一

用户已经看到表格；问一句中文，列出 3 个选项：

```
当前 round X / max_rounds Y。要怎么走？
  1. 「保留 N, M, ...」 → 直接写盘成 IdeaCard
  2. 「重新生成一批，更 [feedback]」 → 抛弃当前，下一轮基于 feedback 出 8 条新候选
  3. 「保留 N, M 改进 + 加 (8-2) 条新的，feedback: [...]」 → 保留 seed + 补新；下一轮重新 panel 评分
（最后一轮 X==Y 时只能选 1）
```

如果 `round_limit_reached: true`，**只列选项 1**，并提示「这是最后一轮」。

### Step 4 — resume

根据用户回应调 `mcp__paic__paic_ideate_step(run_id=..., action=..., keep=..., feedback=..., n_more=...)`：

| 用户意图 | tool 调用 |
|---|---|
| "保留 1, 3" | `action="finalize", keep=[1, 3]`（feedback 可省，会进 feedback_log） |
| "保留全部" | `action="finalize", keep=[0, ..., N-1]`；keep 省略默认 keep all |
| "重新生成，要更应用导向" | `action="regenerate", feedback="更应用导向"` |
| "保留 0, 2 改进 + 加 6 条新的" | `action="refine", keep=[0, 2], feedback="..."` |

### Step 5 — 处理响应

- `status="awaiting_input"`：又进了下一轮的 await，回 step 2 渲染新 drafts
- `status="done"`：写盘完成。渲染 `finalized_ids` + 提示 `下一步 /paic-experiment <idea_id>`
- `warning="round_limit_reached_force_finalize"`：用户在 round limit 后试图 regenerate；工具强制 finalize 了。告诉用户"这是最后一轮，已直接保存当前选中的"

## Style
- 中文叙述。表格里的 title / one_liner 保留原文（英文）。
- **红旗 / 分歧不是阻断**——展示给用户判断，不要自己删 draft。
- **`persona_breakdown` 不展示完整**，但用户问"为什么这个分这么低"时，可以贴对应 persona 的 rationale。
- 用户说"全部" / "都要" / 没回应 → 等价 `action="finalize"`（不传 keep）。
- 用户说"再来一批" / "重新生成" / "regenerate" → `action="regenerate"`；要追问简短 feedback（"以什么方向？"）。
- 用户说"改进这几条" / "refine" / "在 X 基础上深挖" → `action="refine"`，keep 必填，feedback 必填。
- **不要替用户挑 seed**——refine 必须用户明示 keep 哪几条。

## 错误处理
- `error: project_not_initialized` → 提示 `/paic-init`
- `error: llm_unavailable` → 标准 LLM 鉴权问题（参考 troubleshooting.md）
- `error: ambiguous_legacy_call`（v1→v2 过渡）→ 用户给了 feedback 没给 action / keep；让用户补一句 "regenerate" 或 "保留 N, M" 后重试
- `error: refine_requires_keep` → action=refine 但没传 keep；问用户改进哪几条
- `error: invalid_action` → action 拼错；valid 只有 finalize / regenerate / refine

## 跨会话恢复
若用户中途关闭 Claude Code，重开后用 `/paic-resume <run_id>` 即可继续；本 skill 内不需要单独处理。**v1 paused run** 升级到 v2 graph 自动透明（PAI-C 内部 `_normalize_state` 升级 checkpoint）。

## 已知陷阱
- **panel 同质化**：4 persona 默认全用 default backend → 评分高度相关。`paic doctor` 会有 `panel routing` 行警告，建议用户在 `~/.paic/config.yaml` 加一条 `routing.overrides.idea_score_reviewer2: openai`（或任何不同 backend）。
- **多轮成本**：1 brainstorm + 4 persona × 3 轮 ≈ 15 LLM calls。refine 模式有 score_cache（同 draft 跨轮不重复评），实际多在 10-12 calls。**不**是 4×N×rounds——每个 persona 一次性评所有 N drafts。
- **refine 把 seed 改坏**：LLM 偶尔会改"过头"。用户可以下一轮直接 finalize 上轮的版本（cache 保留分数）来"撤销"。
- **`grounded_in` 校验缺失**：LLM 写的 paper_id 可能错；finalize 不验证。后续可能加（与 `/paic-draft compose` 的 cite_keys_in_library guard 同质）。
