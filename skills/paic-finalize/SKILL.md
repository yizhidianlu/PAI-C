---
name: paic-finalize
description: Run the final paper-level quality gate before declaring a draft ready. Eight paper-level checks (undefined cites/refs, unresolved TODOs, duplicate paragraphs, contribution-list consistency, section length balance, unsupported strong claims, numeric claim provenance, optional LaTeX compile warnings). Use when the user says "checking before submission" / "draft ready?" / "final pass" / "跑一下全局检查 / 提交前 quality gate".
allowed-tools: mcp__paic__paic_quality_gate_run, mcp__paic__paic_workspace_status
---

# /paic-finalize — paper-level quality gate

## When to use

- 用户说"提交前最后扫一遍" / "draft ready 了吗" / "checking final coherence" / "跑下 quality gate"。
- `/paic-draft polish` + `/paic-review` 都跑完之后，提交 / 生成 PDF 之前。

## What you do

1. **跑 gate**：
   ```
   mcp__paic__paic_quality_gate_run(project_dir=<cwd>)
   ```
   返回 `{passed, issue_count, issues, overrides}`。每个 issue 含 `kind` / `severity` / `target` / `detail` / `actionable_fix`。

2. **渲染**（中文 + 英文术语）：
   - 顶部一行：「✓ Quality gate PASSED, M issues (info/minor only)」 或 「✗ Quality gate FAILED — N major/blocker issues」
   - 按 severity 分组（blocker → major → minor → info）。每条一行：
     ```
     [<severity>] <kind> @ <target>
       问题: <detail>
       修法: <actionable_fix>
     ```

3. **决定下一步**：
   - 全过 → 「✓ 可以 generate PDF 了。`/paic-draft sync_overleaf`（如果配了 Overleaf）或本地 `latexmk`」。
   - 仍有 blocker → 一条一条让用户处理。建议优先级：
     - `undefined_cites_refs` blocker → 最先处理，否则 LaTeX 编译就报错。
     - `unsupported_claims` major → claims.yaml 里改 status 或加 supporting_*。
     - `numeric_provenance` major → 给 numeric claim 加 experiment_id 或 cite。
     - `contribution_consistency` major → 改 abstract / intro / conclusion 让贡献条数一致；paper_plan.yaml 是 ground truth。
     - `unresolved_todos` major → 处理或显式 override（见下）。
     - `duplicate_paragraphs` minor → 改写一段或换成"as discussed in §X"反向引用。
     - `section_length_balance` minor → 调长度。
   - 用户说「这条我接受了，跳过」→ 用 overrides 重跑：
     ```
     mcp__paic__paic_quality_gate_run(
       project_dir=<cwd>,
       overrides=["unresolved_todos"],  # 或别的 kind
     )
     ```
     **要求用户给一行 reason** 记到 paper_plan 的 `open_todos` 里（手动改 paper_plan.yaml 或运行 `/paic-paper-plan` update）。
   - 用户说「跑 LaTeX 编译检查」→ 加 `compile_check=true`。Phase 10 当前是 stub（不真跑 latexmk），告诉用户暂未实装、需要后续 issue。

## Style

- 中文叙述。issue 里的 `target` (section 名 / claim_id 等) 保留英文 / id。
- 优先暴露 blocker — 用户卡 deadline 时不要把 minor 推到最前。
- 每条 issue 有 actionable_fix 字段：直接复读它，不要再 paraphrase。

## 已知陷阱

- **gate 默认不跑 LaTeX 编译**：phase 10 stub `latex_compile_warnings` 永远返回 []。要真编译需要后续接入 latexmk；目前请用本地 `latexmk -pdf` 兜底。
- **contribution_consistency 是 heuristic**：用 `\item` 计数 + "first/second/third" 等关键字。abstract 写成自然语言段落（不是 list）就检测不出。要更严：手改 paper_plan.yaml 让条数明确，三段都用 itemize。
- **paper_plan / claims.yaml 缺失**：好几个 check 静默跳过（contribution_consistency / section_length_balance / unsupported_claims / numeric_provenance）。让用户先跑 `/paic-paper-plan` 和 `/paic-draft compose`（compose 自动 extract claims）。
- **override 不是 status**：`overrides=["kind"]` 只是 silently 跳过那一类 issue 的本次输出。**不影响 yaml 上的状态**——下次 gate 仍会复现这条 issue。永久解决要改源数据。
