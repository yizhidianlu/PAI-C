---
name: paic-revision-coach
description: Parse unstructured external reviewer comments (emails, PDFs, bullet lists, mixed multi-reviewer blocks) into a prioritized Revision Roadmap + RevisionTask queue. Standalone — works without prior /paic-review run. Triggers on "审稿意见", "R&R", "reviewer comments", "收到审稿", "revision roadmap", "解析审稿".
allowed-tools: mcp__paic__paic_revision_parse_external, mcp__paic__paic_revision_extract_persist, mcp__paic__paic_revision_list, mcp__paic__paic_revision_apply, mcp__paic__paic_revision_resolve
---

# /paic-revision-coach — external reviewer comment parser (ARS-fusion P0-2)

## When to use

- 用户拿到外部审稿（journal email / conference PDF / R&R 邮件），不是 `/paic-review` 自家产生的 → 来这里
- 想要 structured Roadmap + RevisionTask 队列，配合 `/paic-revision-apply` / `/paic-revision-resolve` 跟踪修订进度
- **不要** 用来跑 PAI-C 自己的 `/paic-review` 输出 — 那个用 `paic_revision_extract`（已存在）

## Flow

### Step 1 — 收集输入

询问用户：
1. 审稿意见原文（**必需**）— 任意格式：email 粘贴 / PDF 粘贴 / 列表 / 段落
2. （可选）论文草稿或某 section 的文本 — 启用 section mapping
3. （可选）编辑信 — editor-promoted 项目自动升 severity=major
4. （可选）round_num — 第几轮 R&R

### Step 2 — 调用解析

```
mcp__paic__paic_revision_parse_external(
    project_dir=<cwd>,
    raw_text=<reviewer comments verbatim>,
    format_hint=<"email"|"bullet_list"|"numbered"|"pdf_paste"|"mixed", optional>,
    paper_draft=<optional draft excerpt for section mapping>,
    editor_decision=<optional editor letter>,
    round_num=<optional int>,
)
```

**两种响应**：

#### A. `mode: "host_orchestration"` (默认)

Directive 给主对话：扮演 `revision_coach` agent（见 `agents/revision_coach.md`），按 6 步 pipeline 解析 → 输出 JSON 匹配 `_ExtractFields` schema。然后调：

```
mcp__paic__paic_revision_extract_persist(
    project_dir=<cwd>,
    extracted=<your JSON>,
    round_num=<metadata.round_num>,
)
```

#### B. 直接返回（cloud / fixed backend）

返回 `{extracted_count, tasks, paths, source: "external_text"}`。

### Step 3 — 渲染 Roadmap

```
📋 Revision Roadmap (round=<N>, source=external_text)
   解析出 <N> 条任务 — major <X> / minor <Y> / info <Z>

P1 (must fix — major):
  · [<task_id>] section=<target_ref> — <summary>
    detail: <detail>
    patch_hint: <patch_hint>
    source: <source_persona>
  ...

P2 (should fix — minor):
  · ...

P3 (consider — info):
  · ...
```

按 severity 分组渲染；severity=major 用 ⚠️ 高亮。

### Step 4 — 询问后续动作

让用户选：
1. 立即开始改 → `/paic-revision-apply <task_id>` 把第一条标 in_progress
2. 生成 Response Letter Skeleton（agent 角色继续：每个 reviewer 一个 section + 每个 task 一个 placeholder reply）
3. 列队等下一轮 → 退出

### Step 5 — 跟踪修订

每改完一条让用户调：

```
mcp__paic__paic_revision_resolve(
    project_dir=<cwd>,
    task_id=<id>,
    resolution_summary="<one-line: 改了什么>",
)
```

任意时刻看进度：

```
mcp__paic__paic_revision_list(project_dir=<cwd>, status="open")  # 还没改
mcp__paic__paic_revision_list(project_dir=<cwd>, status="resolved")  # 已改
```

## 错误处理

- `error: project_not_initialized` → `/paic-init` 先建项目
- `error: raw_text_required` → 让用户把审稿意见贴进来
- `error: raw_text_too_short` → 用户可能只贴了一行，确认是不是完整意见
- `error: llm_unavailable` (cloud path only) → 检查 LLM backend 配置

## Style

- 中文叙述。task_id / target_ref / severity / source_persona 保留英文。
- 解析后**先让用户审一遍**再开始改 — 解析有可能误分类，让用户校对一次比改完发现错好。
- Response Letter Skeleton 是 opt-in，不要每次都自动生成（避免冗长输出）。

## 已知陷阱

- **多轮 R&R**: 每轮传不同 `round_num`，`paic_revision_list(round_num=N)` 才能按轮过滤。
- **同一 reviewer 跨轮**: 解析无法追踪 R1 在 round 1 vs round 2 的同一关切。需要的话用户在 `detail` 里手动留 trail。
- **Positive 评论被丢弃**: 这是设计决定 — Positive 不产生 task。要在回复信里 acknowledge 的话用户自己加。
- **Section mapping 启发式**: 没传 `paper_draft` 时按关键词路由，可能误判。建议带 draft 调一次提高准确度。
- **editor_decision 升级**: 只有当 editor 信里**明确引用**某 reviewer 评论时才会升级。模糊的 "address all major comments" 不会触发自动升级。
