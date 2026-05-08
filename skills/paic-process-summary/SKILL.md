---
name: paic-process-summary
description: Render the paper creation process record as markdown — pipeline timeline + Material Passport boundaries + LangGraph runs + latest integrity report. Pure render, no LLM call. Borrowed from ARS Stage 6 PROCESS SUMMARY. Triggers on "process record", "paper creation timeline", "AI usage 报告", "总结这次写作过程".
allowed-tools: mcp__paic__paic_process_summary_generate
---

# /paic-process-summary — paper creation process record (ARS-fusion P3-2)

## When to use

- /paic-pipeline 跑完最后一 stage 后（Stage 10 FINALIZE）— 自动产出本次 pipeline 的 process record
- 用户问"这次写作 AI 都干了什么 / 走了多少 stage / 用了哪些 cite verify"
- 投稿前把过程作为 supplementary material 附上
- 需要 retrospective：哪些 stage 用户接受得多 / 哪些 push back 多

**不要用来**：替代 disclosure 段落（用 `/paic-disclosure`）；替代 integrity 验证（用 `/paic-integrity`）；替代 collaboration depth observer（那是 advisory，跑在 pipeline orchestrator 内部）。

## Flow

### Step 1 — 调用

```
mcp__paic__paic_process_summary_generate(
    project_dir=<cwd>,
    write_to="drafts/process_summary.md",   # 可选，自动写盘
    include_integrity=true,                   # 默认 true，false = 不带 integrity 段
)
```

返回：

```json
{
  "ok": true,
  "content": "# Paper Creation Process Record\n\n...",
  "sections_present": ["pipeline.yaml", "passport.yaml", "runs.yaml", "state/integrity_report.yaml"],
  "project_dir": "...",
  "written_to": "..."   // when write_to supplied
}
```

`sections_present` 列出实际读到的 state 文件 — 缺哪个就在报告里出 placeholder 行（不报错）。

### Step 2 — 渲染给用户

把 `content` 直接 markdown 渲染给用户。报告 4 段：

1. **Pipeline timeline** — 11-stage 历史（from → to + checkpoint kind + verdict + deliverables 计数 + passport hash）
2. **Material Passport** — 跨会话 boundary / resume entries + awaiting_resume 列表
3. **LangGraph runs** — 最近 20 条 ideate / experiment / review run
4. **Latest integrity report** — 通过/未通过 + severity 分布 + by kind top-10 + notes + override 状态

末尾 footer 列出读取的 state 文件 + caveat（dialogue 没捕获 / collaboration depth advisory / plagiarism heuristic）。

### Step 3 — 后续动作

- 用户可作为 supplementary material 附进 supplementary appendix（本身就是 markdown，pandoc 直接转 docx / pdf）
- `/paic-disclosure` 跑前用 process summary 作为输入帮 user 回忆 AI 工具使用清单
- 投稿后做 retrospective 看哪些 stage 是 high-vigilance，哪些是 high-delegation

## 错误处理

- `error: project_not_initialized` → 先 `/paic-init`
- 任意单 source 缺失 → 报告里出 placeholder 行，不报错（设计如此）

## Style

- 中文叙述。markdown content 内英文（原样输出，不翻译）。
- 默认 `include_integrity=true`；用户说"不要 integrity 段"才传 false。
- write_to 默认不传 — 让用户先看一眼 content 满意了再二次调用 + write_to。

## 已知陷阱

- **dialogue 没捕获**：报告基于 state 文件（pipeline.yaml + passport.yaml + runs.yaml + integrity_report.yaml）；用户与 LLM 之间的对话内容不在范围内。要回溯具体对话需查 Claude Code session transcripts。
- **collaboration depth scores 不在报告里**：collaboration_depth observer (P2-2) 是 advisory，跑在 pipeline orchestrator 内部 checkpoint 时；不写盘到 state 文件，所以本报告读不到。要单独看的话直接看对话 transcript。
- **LaTeX→PDF 渲染未实现**：V1.1 ship markdown only；要 PDF 用 `/paic-format-convert --target pdf` 二次转。
- **runs 截断到最近 20**：长 pipeline 可能跑出 50+ ideate/experiment runs；报告只显示最近 20 条，前面会出"+ N earlier runs not shown" 说明。
- **integrity 报告是 snapshot**：永远显示 `state/integrity_report.yaml` 的最新一次 — 跨多次 integrity gate run 的对比需要用户自己保存历史 yaml。
