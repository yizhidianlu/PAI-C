# Paper-quality gate

> **Feature** · `/paic-finalize` 提交前 8 类 paper-level 一致性检查 —— `overrides=` 语义、决策树、修复路径。

`/paic-finalize` 是提交前的最后一道**论文级**检查。它跑 8 类 paper-level 校验，把 LaTeX 语法之外的全局一致性问题暴露出来。

PAI-C 在 `/paic-draft` 写盘时已经把 LaTeX **语法**层的硬错（`\cite{}` 不在白名单、`\begin/\end` 不配对、大括号不平衡）拦在外面；`/paic-finalize` 检查的是**论文级一致性**（contribution 数对不对、numeric 有没有出处、强声明有没有支持）。两者互补，发版前都要过。

> Note: 跑 `/paic-finalize` 之前，跑过 `/paic-paper-plan` + `/paic-draft compose` 才能让大部分检查发挥作用。`paper_plan.yaml` / `claims.yaml` 缺失时部分检查会静默跳过。

---

## 输出形态

每条 issue 含：

```yaml
kind: <8 类之一>
severity: blocker | major | minor | info
target: "01_intro" | "CL3" | "abstract/intro/conclusion"
detail: "..."
actionable_fix: "..."
```

`passed=true` 当且仅当**没有 major / blocker** 严重度的 issue 幸存（info / minor 仅供参考）。

---

## 8 类检查

### 1. `undefined_cites_refs`

**触发**：

- `\cite{key}` 的 `key` 不在 `<project>/.paic/library/selected.yaml` 里 → severity=blocker
- `\ref{label}` 在所有 `drafts/sections/*.tex` 都找不到对应的 `\label{label}` → severity=major

**修法**：

- 缺 cite：跑 `/paic-search` + `/paic-ingest` 把缺的 paper 入库；或重跑 compose 让 LLM 重选。
- 缺 ref：去对应 figure / table / equation 加 `\label{}`；或删 `\ref`。

### 2. `unresolved_todos`

**触发**：section 里有 `\todo{...}` 或 `% TODO:` 注释 → severity=major（一行只算一条 issue，但 detail 列计数）。

**修法**：处理或显式接受。接受时 `overrides=["unresolved_todos"]` 让本次 gate 跳过这类，并在 `paper_plan.yaml.open_todos` 留 reason。

### 3. `duplicate_paragraphs`

**触发**：跨所有 section 任意两个段落 rapidfuzz `token_set_ratio >= 85` → severity=minor。短段（< 80 字）自动跳过。

**修法**：改写其中一段，或换成「as discussed in Section X」反向引用。

### 4. `contribution_consistency`

**触发**：abstract / intro / conclusion 中通过 `\item` 数 + "first / second / third" 等连接词数出贡献条数，与 `paper_plan.contributions` 长度有任何不一致 → severity=major。

**修法**：以 `paper_plan.yaml` 为 ground truth，三段同步条数。若 paper_plan 也错，先 `/paic-paper-plan` update 改贡献清单。

> Note: 这是**启发式**计数。abstract 写成自然语言段落（不用 itemize / enumerate）时检测不到；要更严格请三段都用 `\begin{itemize}...\end{itemize}`。

### 5. `section_length_balance`

**触发**：某 section 字数 / 该 section 在 `paper_plan.section_plan[*].target_words` 之比落在 `[0.3, 2.0]` 之外 → severity=minor。

**修法**：补内容或精简。或调 paper_plan 的 target_words（可能是 plan 不实际）。

### 6. `unsupported_claims`

**触发**：`claims.yaml` 中 `status="needs_evidence"` 且 `type` 是 novelty / comparative / numeric / result（**强声明**）的 claim → severity=major。

**修法**：

- 加 `supporting_papers` / `supporting_experiments` 到 claim → 改 status 为 supported
- 标 `\todo{}` 在 LaTeX 段落里、claim status 改 todo
- 取消该声明、status 改 rejected

### 7. `numeric_provenance`

**触发**：`type="numeric"` 的 claim 没 `supporting_experiments` 也没 `required_citations`（同时 claim 文本里确实出现数字）→ severity=major。

**修法**：

- 内部数字：attach experiment_id（claim 的 supporting_experiments 加该实验 id）
- 外部数字（引用别的 paper）：attach cite_key 到 required_citations

### 8. `latex_compile_warnings`（opt-in，预留）

**触发**：当 `--compile-check` 启用时本应解析 LaTeX 编译日志。**当前 stub**——永远返回 `[]`，后续版本实装。

**临时方案**：本地 `latexmk -pdf drafts/main.tex` 兜底，或交给 Overleaf 编译查看（[overleaf-sync.md](overleaf-sync.md)）。

---

## Overrides 用法

调用时附加 `overrides=["unresolved_todos", "section_length_balance"]` 把列出 kind 的 issue 从输出里**静默删除**，不计入 `passed` 判定。常用于：

- 已知 TODO，赶 deadline，先发再说
- 评审人接受的章节长度偏差
- 某条 strong claim 的支持文献还在 review

> ⚠ **Blocker 不可被 override**：`severity=blocker` 的 issue 无论 kind 是否在 `overrides` 列表里都会**保留**，并写到响应的 `overrides_rejected: [{kind, severity}]` 字段告诉你「这条 override 没生效」；`passed` 在有 blocker 幸存时永远是 `false`。这是 v0.2 的硬墙，避免「形式上 evidenced、实质未 grounded」的论文 silent 通过。

> Warning: override 不持久化、不改 yaml 状态。下次 `/paic-finalize` 还会复现这条 issue（除非源数据改了）。要让 issue 真的消失，请改源数据：claim status / paper_plan / 删 TODO。

建议把每次 override 的 reason 写进 `paper_plan.yaml.open_todos`，作为提交后 followup 的 trail。

### `--strict` / `strict=true`

提交前最后一遍 / CI 用，**忽略所有 overrides**，每条 issue 都计入 `passed` 判定。`/paic-finalize` SKILL 识别用户原话里「严格 / strict / 提交前最后一遍 / 用 strict 跑」等关键词时自动启用。CI 集成例：把 `paic_quality_gate_run(strict=True)` 作为 release pipeline 的 gate。

---

## 决策树

跑出 issue 列表后按 severity 决定：

```text
issue.severity == blocker   →  必修，否则 LaTeX 都编译不过（cite_key 不存在等）
issue.severity == major     →  强烈建议修；deadline 紧时 override 并在 open_todos 留 reason
issue.severity == minor     →  自由决定，多半是文风 / 长度问题
issue.severity == info      →  仅作参考
```

issue 与对应工具的接力关系：

| issue.kind | 回到哪个工具 |
|---|---|
| `undefined_cites_refs` | `/paic-ingest` 加 paper；或 `/paic-draft compose` 重选 cite |
| `unresolved_todos` | 直接编辑 `drafts/sections/*.tex` |
| `duplicate_paragraphs` | `/paic-draft polish --mode tighten` 或手改 |
| `contribution_consistency` | `/paic-paper-plan` update + `/paic-draft compose` 重写不一致 section |
| `section_length_balance` | `/paic-draft polish --mode expand|tighten` |
| `unsupported_claims` | `/paic-draft compose` 加 cite；或编辑 `claims.yaml` 改 status |
| `numeric_provenance` | 编辑 `claims.yaml` attach experiment_id 或 cite_key |
| `latex_compile_warnings` | 本地 `latexmk` 兜底 |

---

## 用法

```text
/paic-finalize
```

PAI-C 跑完检查后按 severity 分组渲染 issues，每条一行：`[<severity>] <kind> @ <target>` + detail + actionable_fix。决定下一步：全过 → 提示生成 PDF；有 blocker → 让用户逐条修；想跳过特定 kind → 重跑带 overrides。

---

## Troubleshooting

| 症状 | 处理 |
|---|---|
| `passed=true` 但有 minor issues | 正常。`passed` 只看 major / blocker |
| `contribution_consistency` 误报 | abstract 用 itemize / enumerate 显式列贡献，或 paper_plan 改条数 |
| `unsupported_claims` 漏报 | 检查 claim 是否漏在 `claims.yaml` 里——compose 后没自动 extract 时让 PAI-C 手动补抽 |
| `numeric_provenance` 误报 | claim text 里数字也算（如版本号「2024」）；不需要时把 claim 标 `status=rejected` |
| `duplicate_paragraphs` 误报跨 abstract 与 intro | abstract 本来就是 intro 浓缩；可 override 该 kind |
| `--compile-check` 没真跑 latexmk | 当前 stub，本地 `latexmk -pdf` 兜底 |

---

## 参考

- 写作侧前置工具：[paper-plan.md](paper-plan.md)
- 提交流程整体：[workflow.md § Paper-quality 高级阶段](workflow.md#paper-quality-高级阶段)
