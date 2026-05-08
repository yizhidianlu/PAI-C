---
name: paic-pipeline
description: Run PAI-C's full 11-stage end-to-end pipeline (init → search → ingest → ideate → experiment → plan → draft → integrity-pre → review → revise → integrity-final → finalize). Light-weight orchestrator; MANDATORY checkpoints at stages 6 / 9 / 10. Triggers ONLY on strong end-to-end signals — "from research to draft", "端到端 paper", "跑一遍完整 pipeline", "把流程串起来". Does NOT trigger when a single SKILL like /paic-search or /paic-finalize would suffice.
allowed-tools: mcp__paic__paic_pipeline_state, mcp__paic__paic_pipeline_advance, mcp__paic__paic_passport_emit, mcp__paic__paic_passport_list, mcp__paic__paic_runs_list
---

# /paic-pipeline — 11-stage end-to-end orchestrator (ARS-fusion P1-4)

## When to use

**触发条件**（必须明确 end-to-end 信号才用）：
- "从零开始写一篇 paper"
- "from research to draft"
- "跑一遍完整 pipeline"
- "把整个流程串起来"
- "端到端 paper"

**不触发**（直接用对应 SKILL 即可）：

| 用户意图 | SKILL |
|---|---|
| 只想搜文献 | `/paic-search` |
| 只想抓个 idea | `/paic-ideate` |
| 只想跑 review | `/paic-review` |
| 只想跑结构 quality gate | `/paic-finalize` |
| 只想跑真伪 gate | `/paic-integrity` |
| 只想转 docx / pdf | `/paic-format-convert` |
| 只想生成 disclosure | `/paic-disclosure` |
| 只想恢复上次 paused 的 run | `/paic-resume` |

## Flow

按 `agents/pipeline_orchestrator.md` 走。简版：

### Step 1 — 检测 entry point

调 `mcp__paic__paic_pipeline_state(project_dir=<cwd>)` 看是否已有 pipeline state。

- `exists=False` → 询问用户当前手上有什么材料
- `exists=True` → 显示 current_stage，问用户是「继续」还是「重启」

### Step 2 — 推荐 mode + budget transparency

按用户偏好（新手 / 经验丰富 / 时间紧）+ 论文长度估算 + cross-model 开关 给一句成本预告：

```
预估全 pipeline：约 N tokens / ¥M。继续？
- Stage 5 DRAFT compose 4-call 默认开（V1.0），可选 --strict=False 退到 1-call 省 4 倍
- Stage 6/9 INTEGRITY 100% citation verify 走 host orchestration WebSearch
```

### Step 3 — Dispatch 各 SKILL

按 stage 顺序调对应 SKILL。**每个 stage 完成都调 `paic_pipeline_advance`** 记录 transition：

```
mcp__paic__paic_pipeline_advance(
    project_dir=<cwd>,
    to_stage=<N>,
    checkpoint_kind="FULL"|"SLIM"|"MANDATORY",
    verdict=<可选 — REVIEW outcome / INTEGRITY pass-fail>,
    deliverables=[<artefact paths>],
    consecutive_continue=<True 当用户说"continue"时>,
)
```

返回里 `is_mandatory: true` + `promoted_to_mandatory: true` 表示 stage 6/9/10 自动升 MANDATORY，**不可绕过**。

### Step 4 — Checkpoint 渲染

按 `last_checkpoint_kind` 选 UX：

- **FULL** → 完整决策面板 + 5 self-check + 3 选项（continue / pause / adjust）
- **SLIM** → 一行 summary + (y/pause/adjust)
- **MANDATORY** → ⚠️ 高亮 + 强制要求用户给 verdict

### Step 5 — 暂停与恢复

用户说"暂停"时（且 `passport.enable_reset_boundary=true`）：

```
mcp__paic__paic_passport_emit(
    project_dir=<cwd>,
    stage=<current>,
    deliverables=[...],
    next_stage=<next>,
    pending_decision=<可选 — 当下个 stage 是 MANDATORY 多分支时>,
)
```

把返回的 `resume_command` 给用户：「下次开新会话，用 `/paic-resume passport=<hash>` 接着跑」。

恢复路径见 [`/paic-resume`](../paic-resume/SKILL.md)。

### Step 6 — 全 pipeline 完成

stage 10 FINALIZE 跑完 → 提示 `/paic-process-summary`（P3，未来）+ 提交。

## Iron rules

1. **每个 stage 都过 paic_pipeline_advance** — 不要 dispatch 多个 stage 才记一次。
2. **MANDATORY 不能跳** — stage 6 / 9 / 10 必须等用户给 verdict。
3. **5 self-check 永远跑** — 哪怕用户说"快点 just go"，也要 silently 跑，发现 concern 才 surface。
4. **Substantive work 交给 SKILL** — 你只 dispatch + checkpoint，不写 paper、不跑 review、不验 cite。

## 错误处理

- `error: project_not_initialized` → 先 dispatch /paic-init
- `error: invalid_stage` → 检查 to_stage（0-10）
- `error: invalid_checkpoint_kind` → "FULL" / "SLIM" / "MANDATORY"
- 子 SKILL 自身的错误 → 透传给用户，不要硬扛

## Style

- 中文叙述。stage label / SKILL name / verdict id / hash 保留英文。
- Checkpoint 渲染区分清晰（用 ━━━ / ✓ / ⚠️ 三种边界）。
- 不要替用户做决定 — 每个 checkpoint 必须用户开口。

## 已知陷阱

- **触发太宽**：模糊指令（"帮我写论文"）应该先问清楚，不要立刻进 pipeline。Pipeline 是 6+ 周工程，单个 SKILL 通常够。
- **跳过中间 stage**：用户说"我已经有 draft 了，直接到 finalize"——必须经过 stage 6 INTEGRITY-PRE（ARS iron rule），除非用户提供之前的 integrity report 且声明内容未改。
- **重复 dispatch**：用户说"跑一遍 stage 5 看看"——别又开个 pipeline run，直接调 `/paic-draft` 就行。
- **passport opt-in**：默认 `passport.enable_reset_boundary=false`。第一次用户说"暂停下次接着跑"时主动提示开启。
- **跨平台 lock**：Windows 用户 lock contention 时报 `passport_lock_timeout`，让用户检查是否有另一进程在跑同一项目（不要静默重试）。
