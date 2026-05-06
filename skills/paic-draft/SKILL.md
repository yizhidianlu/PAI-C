---
name: paic-draft
description: LaTeX writing — v0.1 fills a venue template (built-in cvpr/neurips/ieee, or any project-local template under .paic/templates/); v0.2 polishes individual sections via LLM rewrite (tighten / clarify / formalize / expand / proofread); v0.3 composes full sections from idea + experiment + library with auto citation alignment. Use when the user says "起 LaTeX 骨架" / "draft the paper" / "改一下 intro" / "compose 一段 related work".
allowed-tools: mcp__paic__paic_draft_fill, mcp__paic__paic_draft_list_templates, mcp__paic__paic_draft_scaffold, mcp__paic__paic_draft_polish, mcp__paic__paic_draft_polish_persist, mcp__paic__paic_draft_compose, mcp__paic__paic_draft_compose_persist, mcp__paic__paic_workspace_status
---

# /paic-draft — LaTeX writing

## Stages

PAI-C ships LaTeX support in **3 stages** — all live now:

- **v0.1: `fill`** — populate a template skeleton from idea + experiment + bib.
- **v0.2: `polish`** — paragraph-level LLM rewrites of an existing section. 5 modes (tighten / clarify / formalize / expand / proofread) + freeform instruction.
- **v0.3: `compose`** — full-section composition from idea + experiment + library with auto citation alignment. 2 modes (from_stub / from_scratch) + freeform instruction.

## Flow — v0.1 fill

1. **Parse the user's request**:
   - Required: an `idea_id` (else ask which one — they can `/paic-status` to see ideas).
   - Required: a `template` name (see step 2 for discovery).
   - Optional: an `experiment_id` (if absent, the method/experiments sections fall back to TODO placeholders — let the user know).

2. **Discover available templates** (always — even if the user named one):
   ```
   mcp__paic__paic_draft_list_templates(project_dir=<cwd>)
   ```
   Returns `{templates: [{name, kind, display_name, target_venue, has_static_assets, overrides_builtin}, ...], templates_dir, project_dir}`.

   - `kind` is `builtin` (cvpr / neurips / ieee, ship in PAI-C) or `user` (under `<project>/.paic/templates/<name>/`).
   - `overrides_builtin: true` means a user template shadows a same-named built-in.

3. **Resolve the template name**:
   - If the user already specified one and it's in the list → use it.
   - If the user said "give me a template" / didn't name one → list every entry as a numbered Chinese menu and ask. Show `display_name` (or `name` if absent), `kind`, and any `target_venue` annotation.
   - If the user said "我想用 ICLR / Nature / TPAMI / ..." and no matching template exists → see step 4 (scaffold path).

4. **(Optional) Scaffold a new template** when the user wants a venue PAI-C doesn't ship:
   ```
   mcp__paic__paic_draft_scaffold(project_dir=<cwd>, name="iclr2026", base="neurips")
   ```
   - Creates `<project>/.paic/templates/<name>/{main.tex.j2, template.yaml}` from the chosen `base` (cvpr / neurips / ieee — pick whichever style is closest).
   - **Tell the user** (Chinese, one paragraph) the next two steps:
     1. 编辑 `<root>/main.tex.j2` —— 把 `\usepackage{neurips_2024}`、title block、作者格式换成目标会议/期刊的（参考 `docs/custom-templates.md`，jinja 占位符如 `{{ idea.title }}` / `{{ project.title }}` 与内置模板共用）。
     2. 把 venue 官方模板包里的 `.sty` / `.cls` / `.bst` 文件拷到同一目录 —— fill 时会自动复制到 `drafts/`，不需要你再手动拖。
   - Wait for the user to confirm they've done it (or ask them to). Then proceed to step 5.

5. **Fill**:
   ```
   mcp__paic__paic_draft_fill(
     project_dir=<cwd>,
     template=<resolved_name>,
     idea_id=...,
     experiment_id=...,
   )
   ```

6. **On success**, render in Chinese:
   - `main.tex` 路径 + 选用模板（标注 `kind=builtin` 还是 `kind=user`）
   - 6 个 sections 列表（00_abstract → 05_conclusion）
   - `refs.bib` 含 N 条 BibTeX 项（列出前几个 cite key）
   - **静态资源**：列出 `static_assets_copied` 里的文件名（如 `iclr2026.sty`、`fancyhdr.sty`、`figures/logo.pdf`）。如果列表为空且这是个内置模板，显式提示「PAI-C 不携带内置 venue 的 .sty/.cls；编译前请把 `<venue>.sty` 拷到 `drafts/`」。
   - **跳过的资源**（如果 `static_assets_skipped` 非空）：一行说明「模板里的 `<file>` 被跳过（PAI-C 自己生成 `main.tex` / `refs.bib`）」。
   - 一行编译建议：「`tectonic drafts/main.tex` 或 `pdflatex` 编译；下一阶段（v0.2）将开放段落润色。」

## Flow — v0.2 polish

When user says "polish 一下 intro" / "改一下方法那段" / "把 conclusion 写正式点" / "expand 把 method 占位补全":

1. **Identify section + mode**:
   - Section: 用户可说 canonical 名（`01_intro`）、alias（`intro` / `method` / `results` / `conclusion`）、或带 `.tex`/路径前缀。
   - Mode: 默认 `clarify`。映射用户中文意图：
     - "压缩 / 砍长" → `tighten`
     - "改清楚 / 改顺" → `clarify`（默认）
     - "更学术 / 更正式" → `formalize`
     - "把 TODO 占位填好 / 写完" → `expand`（需要 `idea_id`，可从 `paic_workspace_status` 找最近的 idea）
     - "改语法 / proofread" → `proofread`
   - 用户给的额外要求（"用第三人称"、"加一个小标题" 等）→ 作为 `instruction` 参数透传。

2. **Call**:
   ```
   mcp__paic__paic_draft_polish(
     project_dir=<cwd>,
     section="01_intro",        # or alias / path
     mode="clarify",
     instruction=<freeform 可空>,
     idea_id=<expand 模式必需>,
     experiment_id=<expand 模式可选>,
     dry_run=false,             # 默认写盘 + 备份
   )
   ```

3. **On success**, render in Chinese:
   - 一行：「✓ polish 完成：drafts/sections/01_intro.tex（mode=clarify）」
   - 备份位置：「备份在 `drafts/sections/01_intro.tex.bak.<UTC>`」
   - **diff 块**：把 `out["diff"]` 直接贴出来（已经是 unified diff 格式）。如果 diff > 50 行，截断到前 30 行加 "...（剩余 N 行省略，完整 diff 见 backup）"。
   - **validation warnings**（如有）：`out["validation"]["warnings"]` 列出来，例如「polish 后长度 <30% 原文」。
   - 一行建议：「不满意可 `cp <backup_path> <section_path>` 还原；或换个 mode 再 polish 一次」。

4. **Host orchestration mode**: 当 `out["mode"] == "host_orchestration"`：
   - 主对话 polish：根据 `out["user_prompt"]` 自己写 polished LaTeX。**严格遵守 prompt 里的约束**——保留所有 `\cite{}` / `\ref{}` / `\label{}` 键，不引入新 cite，保持 `\begin/\end` 配对。
   - 写完调：
     ```
     mcp__paic__paic_draft_polish_persist(
       project_dir=<cwd>,
       section=<同上>,
       polished=<你写的完整 polished 文本>,
       original_hash=<out["original_hash"]>,
     )
     ```
   - persist 会跑同样的结构校验。验证失败 → 改输出重试；hash 不一致（用户中途改了文件）→ 重 polish。

### Polish / compose 共用错误处理
- `error: section_not_found` → 列出 `drafts/sections/` 当前存在的 `.tex` 文件给用户选。
- `error: section_empty` (polish only) → 提示先 fill 或先 compose。
- `error: empty_library` (compose only) → 提示先 `/paic-search` + `/paic-ingest`。
- `error: invalid_mode` → 显示 `valid_modes` 让用户重选。
- `error: latex_validation_failed` → **不要慌**，原文件没动。展示 `validation` 字段告诉用户哪一项没通过（cite / begin-end / brace）。compose 模式下还会有 `cite_keys_missing_from_library` 字段——告诉用户哪些 cite 不在库里。建议换 mode 或 ingest 缺失的论文后重试。`{polished,composed}_preview` 可以贴前 1500 字给用户看 LLM 输出了啥。
- `error: original_hash_mismatch`（host persist）→ 用户在主对话生成期间手工改了文件。提示重跑流程。
- `error: llm_unavailable` → 标准 LLM 鉴权问题；和 summarize 一样的应对。

### 多次 polish
用户可以连续 polish 同一 section（不同 mode），每次都创建新备份。例如：
```
你: polish 01_intro --mode tighten
[Claude 调 polish, 写盘]
你: 太短了，再 expand 一下
[Claude 再调 polish mode=expand instruction="刚才太短了，扩回原长度"]
```
Multi-pass 是合法的。

## Flow — v0.3 compose

When user says "写一段 related work" / "compose 整个 intro" / "把 method 段从头生成" / "compose 02_related":

1. **Identify section + mode**:
   - Section: 同 polish 的 alias 解析（intro / related / method / experiments / conclusion / abstract）。
   - Mode: 默认 `from_stub`（把当前 section 文件内容作 outline 扩写）。用户说"从头" / "fresh" / "ignore the stub" → `from_scratch`。
   - **idea_id**：高度推荐——compose 主要按 idea 对齐论点。从 `paic_workspace_status` 找最新的 idea 或让用户指定。
   - **experiment_id**：method / experiments section 强烈推荐。
   - target_words：用户说"~800 字"或"intro 控制在 1 页"→ 转成 int 透传；不指定时 PAI-C 用 section 默认值。
   - instruction：freeform。

2. **Pre-flight check**:
   - 看 `paic_workspace_status` 的 `library_count`。如果是 0 且要 compose intro/related/experiments → 先建议 `/paic-search` + `/paic-ingest`，再来 compose。
   - 提醒用户：compose 会**覆盖**整个 section 文件（备份会留）。

3. **Call**:
   ```
   mcp__paic__paic_draft_compose(
     project_dir=<cwd>,
     section="02_related",
     mode="from_stub",
     idea_id="<id>",
     experiment_id="<id>",       # method/experiments 推荐
     target_words=<int 或 null>,
     instruction=<freeform 可空>,
     dry_run=false,
   )
   ```

4. **On success**, render in Chinese:
   - 一行：「✓ compose 完成: drafts/sections/02_related.tex（mode=from_stub, section=related）」
   - **引用统计**：「使用了 N 个 cite (`arxiv_xxx`, `doi_yyy`, ...)（从 library 共 M 篇里挑选）」
   - 备份位置
   - **diff 块**：把 `out["diff"]` 截断到前 30 行展示。
   - validation warnings（如有）。
   - 一行：「不满意可 `cp <backup_path> <section_path>` 还原；或重 compose 一次（每次会留新备份）」。

5. **On `latex_validation_failed`**:
   - 显示 `cite_keys_missing_from_library` 列表（"LLM 引用了 [foo, bar] 但 library 里没有"）。
   - 提示用户：(a) 把这些论文 ingest 进库再 compose；(b) 或者换 mode/instruction 重试。
   - 如果 `validation.begin_end_balanced` / `brace_balanced` 不通过：展示前 1500 字的 `composed_preview`，建议重试。
   - **不要慌**——原文件没动。

6. **On `error: empty_library`**:
   - 提示用户先 `/paic-search "你的方向"` + `/paic-ingest <相关论文>` 再 compose。abstract / conclusion 的 compose 不需要 library，可以照跑。

7. **Host orchestration mode**: 当 `out["mode"] == "host_orchestration"`：
   - 主对话 compose：根据 `out["user_prompt"]` 自己写 LaTeX section。**严格只用** `out["library_cite_keys"]` 列表里的 cite key——不能编造。
   - 写完调：
     ```
     mcp__paic__paic_draft_compose_persist(
       project_dir=<cwd>,
       section=<同上>,
       composed=<你写的完整 LaTeX>,
       original_hash=<out["original_hash"]>,
     )
     ```
   - persist 校验 cite + 结构 + hash。失败响应同 `paic_draft_compose` 错误集。

### compose vs polish — 怎么选

| 用户意图 | 用 compose 还是 polish |
|---|---|
| "把 fill 留下的 TODO 占位写成正经段落" | **compose `from_stub`**——会主动加引用 |
| "现有段落改清楚 / 压缩 / 改正式" | **polish**（tighten / clarify / formalize） |
| "写一段全新的 related work" | **compose `from_scratch`** |
| "expand 当前 section 但不加新引用" | **polish `expand`**——polish 不引入新 cite |

如果用户犹豫，建议这条流：fill → compose（写出正经段落） → polish（细调）。

### 多次 compose
和 polish 一样，重 compose 同一 section 是合法的（每次新备份）。常见用法：先 `from_scratch` 起初稿，看 diff 不满意 → 加 `instruction="paragraph 1 多举例; paragraph 2 改成 2-column 表格"` 重 compose。

## Error handling — fill / scaffold
- `error: unknown_template` → 响应里有 `available` 列表 + `hint`。把列表展示给用户、提示也可以用 scaffold 派生。
- `error: not_found` → 提示先 `/paic-experiment <idea_id>` 或检查 idea_id。
- `error: project_not_initialized` → 先 `/paic-init`。
- `error: template_already_exists`（来自 scaffold）→ 提示用户换名字或先 `rm -rf .paic/templates/<name>`。
- `error: unknown_base`（来自 scaffold）→ 显示 available 内置模板列表（cvpr/neurips/ieee）。

## Style
- 中文叙述。LaTeX 文件路径与命令保留原样，不要翻译。
- **不要主动建议**用户改 .tex 文件 —— 让用户决定改不改；polish 工具是有意触发，不是被推送。
- 输出尽量紧凑，列表式而非散文。
- **scaffold 后**——如果 user 让你 fill 但还没编辑模板，先**确认**他/她把 `\usepackage{...}` 改对了；否则编译会因 `iclr2026.sty 不存在` 之类的错误失败。
- 用户问"PAI-C 支持哪些会议"时，直接给 list_templates 的结果（builtin + user 全列），不要凭印象答。
- **polish 完后**——把 diff 给用户看，让用户判断要不要保留；不要替用户表态"看起来不错"。如果用户不满意，提示 `cp <backup> <section>` 还原。

## 已知陷阱
- **scaffold 出来的模板用的是 base 的 `\usepackage{...}`**：用户必须手动改成目标 venue 的样式包，否则编译会失败。SKILL 里 step 4 已经强调了这一点。
- **venue 官方 main.tex 与 PAI-C 的 main.tex.j2 结构差异**：用户从 venue 官方下载的 main.tex 可能没有 `\input{sections/01_intro}` 这种结构。`docs/custom-templates.md` 提供了从 venue 包改写到 PAI-C jinja 模板的实战例。
- **静态资源同名冲突**：用户模板里的 `refs.bib` / `main.tex` 会被自动跳过（PAI-C 自己生成）；`static_assets_skipped` 字段会列出这些。
- **完整 jinja 变量参考**：`{{ project.title }}` / `{{ idea.title }}` / `{{ idea.one_liner }}` / `{{ idea.proposed_approach }}` / `{{ experiment.proposed_method }}` 等等——详见 `docs/custom-templates.md`。
- **Jinja `\lstset{%` 冲突**（含字面 `%` 的 venue 模板）：venue 自带的 `main.tex` 经常用 `\lstset{%` 注释续行，但 `{%` 被 Jinja 当成控制块开头会报 `TemplateSyntaxError`。两种修法：
  1. 模板里把 `{%` 改写成 `{{ '{%' }}`，对应的 `%}` 改成 `{{ '%}' }}`
  2. 在该段外包 `{% raw %}...{% endraw %}` 让 Jinja 整段透传（适合大段 `\lstset` / `\tikzset` 配置）
  scaffold 出来的模板若编译报 `unexpected '%'`，多半就是这个问题。
- **polish 偶发改写 cite_key 标点**：LLM 极小概率会把 `\cite{doi_10_3389_fnins_2023_1276067}` 写成 `\cite{doi_10.3389_fnins_2023_1276067}`（恢复了 DOI 里的句号）。命中率约 1/6。**预防方法**：调 `paic_draft_polish` 时显式传 `instruction="Preserve all \\cite{} keys character-for-character — they are programmatic identifiers, not text. Underscores must remain underscores; never reintroduce dots or change case."`。失败时 `paic.latex.guard.cite_keys_preserved` 会检测到（新 cite key 不在 orig 集合里）并拒绝，看到 polish 拒绝时直接 retry 即可。
