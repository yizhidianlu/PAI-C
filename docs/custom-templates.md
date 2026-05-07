# 自定义 LaTeX 模板

> **Optional** · 项目本地放 ICLR / Nature / TPAMI / 校刊等 venue 模板 —— `paic_draft_scaffold` 从内置模板派生，静态 `.sty` / `.cls` 自动拷贝。

PAI-C 内置了 3 个 venue 模板：`cvpr` / `neurips` / `ieee`。若你的目标会议 / 期刊不在这三个里（ICLR / ICML / AAAI / Nature / TPAMI / ACL / EMNLP / 校刊 ……），可以在**项目本地**目录 `<project>/.paic/templates/<name>/` 下放自己的模板，`/paic-draft fill <name>` 会自动识别并使用。

> **PAI-C 不替换内置模板**——`cvpr` / `neurips` / `ieee` 永远可用。自定义模板是**叠加**的；同名时项目本地优先（被标记为 `overrides_builtin: true`）。

## 最快路径（推荐）：scaffold + 改

PAI-C 提供 `paic_draft_scaffold` MCP 工具，从内置模板派生一个起点：

```text
你: /paic-draft 帮我准备一个 ICLR 2026 模板
Claude: （检测到没有 iclr2026 模板）我可以基于内置 neurips 给你 scaffold 一个：
你: 好
Claude: → mcp__paic__paic_draft_scaffold(name="iclr2026", base="neurips")
        → .paic/templates/iclr2026/main.tex.j2 + template.yaml 生成
        请编辑 main.tex.j2 把 \usepackage{neurips_2024} 改成 ICLR 的样式包，
        并把 ICLR 官方模板包里的 .sty 复制到这个目录。
```

接下来手工做 2 件事：

### Step 1：编辑 `main.tex.j2` 适配 venue

打开 `.paic/templates/iclr2026/main.tex.j2`，里面是 NeurIPS 骨架的拷贝。你需要：

| 要改的 | 内置 NeurIPS 写法 | 你要换成 |
|---|---|---|
| 样式包 | `\usepackage[final]{neurips_2024}` | `\usepackage{iclr2026_conference}` |
| 标题宏 | `\title{...}` 内的格式 | venue 要求的（部分 venue 用 `\Title` / `\maketitle` 时机不同） |
| 作者块 | `\author{Author One \\ Affiliation \\ ...}` | venue 模板里的写法（双盲会议常见 `\author{Anonymous}`） |
| Bib 风格 | `\bibliographystyle{plainnat}` | venue 推荐的（`iclr2026_conference` / `acl_natbib` / etc.） |

**关键约束**：保留 `\input{sections/00_abstract}` 直到 `\input{sections/05_conclusion}` 这 6 行 —— PAI-C 渲染的 6 个 section 文件依赖它。若删掉某个 `\input{...}`，对应的 section 文件还是会生成，只是不会被 main.tex 引用。

### Step 2：把 venue 官方静态文件拖进去

从 venue 官网下载模板包后，把里面的 `.sty` / `.cls` / `.bst` 文件**复制到** `.paic/templates/iclr2026/`：

```text
.paic/templates/iclr2026/
├── main.tex.j2                      # ← 你刚改完的 jinja 骨架
├── template.yaml
├── iclr2026_conference.sty          # ← venue 官方
├── fancyhdr.sty                     # ← venue 官方
├── natbib.sty                       # ← venue 官方
└── iclr2026.bst                     # ← venue 官方（如果有）
```

`fill` 时 PAI-C 会把 `*.sty` / `*.cls` / `*.bst` / 图片文件等**全部自动复制到** `drafts/`，无需手动复制。

### Step 3：Fill

```text
你: /paic-draft fill iclr2026 idea_id=01HX...
```

输出：

```text
[OK] template=iclr2026 (kind=user)
main.tex:    drafts/main.tex
refs.bib:    drafts/refs.bib  (3 BibTeX 项)
sections:    6 个文件（drafts/sections/00_abstract.tex ...）
静态资源已拷贝: iclr2026_conference.sty, fancyhdr.sty, natbib.sty, iclr2026.bst
编译: tectonic drafts/main.tex
```

---

## 慢路径：从 venue 包从头改写

若希望**完全自主**而不是 scaffold 派生，可以手建目录：

```text
mkdir -p .paic/templates/myvenue
```

最少要写两件东西：

### 1. `main.tex.j2`（必需）

把 venue 的 `main.tex` 改成 jinja 模板。能用的占位符：

| Jinja 表达式 | 来源 | 说明 |
|---|---|---|
| `{{ project.title }}` | `.paic/project.yaml` | 项目标题 |
| `{{ project.venue }}` | `.paic/project.yaml` | 目标 venue |
| `{{ idea.title }}` | `.paic/ideas/<id>.yaml` | IdeaCard 标题 |
| `{{ idea.one_liner }}` | 同上 | 简短摘要 |
| `{{ idea.motivation }}` | 同上 | 动机段 |
| `{{ idea.proposed_approach }}` | 同上 | 方法概要 |
| `{{ idea.novelty_claim }}` | 同上 | 新颖性宣称 |
| `{{ idea.expected_contribution }}` | 同上 | 预期贡献 |
| `{{ experiment.proposed_method }}` | `.paic/experiments/<id>.yaml` | 完整方法描述 |
| `{{ experiment.research_questions }}` | 同上 | 研究问题列表（用 `{% for q in ... %}`） |
| `{{ experiment.metrics }}` | 同上 | metric 列表 |
| `{% for grp in cited_papers %}` | 自动生成 | 分组的引用论文 |

**最少示例**（够 fill 跑通）：

```jinja2
\documentclass{article}
\usepackage{myvenue_style}
\title{ {{ project.title | default('TODO', true) }} }
\author{Anonymous}
\begin{document}
\maketitle
\input{sections/00_abstract}
\input{sections/01_intro}
\input{sections/02_related}
\input{sections/03_method}
\input{sections/04_experiments}
\input{sections/05_conclusion}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
```

> **Jinja 语法注意**：LaTeX 里的 `{` `}` 与 jinja 的 `{{ }}` 容易冲突。需要在输出里写**字面**花括号时，用 `{{ '{' }}` `{{ '}' }}` 包起来。例：
>
> ```jinja2
> \title{{ '{' }}{{ project.title }}{{ '}' }}
> ```
>
> 这写法在内置模板里也能看到（`src/paic/latex/templates/neurips/main.tex.j2:21`）。

### 2. `template.yaml`（可选）

```yaml
display_name: My Venue 2026
target_venue: ACL 2026
description: Anonymous-by-default; uses myvenue_style.sty
```

`/paic-draft list` 会展示 `display_name`，比纯 directory name 友好。

### 3. （可选）覆盖个别 section

如果 venue 对 related-work 或 conclusion 有特殊格式，在 `<root>/sections/<name>.tex.j2` 放同名文件即可。fill 时 PAI-C 优先用你的版本，找不到再 fall back 到内置 `_shared/sections/`。Section 名固定是这 6 个：

- `00_abstract`
- `01_intro`
- `02_related`
- `03_method`
- `04_experiments`
- `05_conclusion`

不能新增 section（v0.1 限制；v0.2/v0.3 可能放开）。如果 venue 要求 `Limitations` / `Broader Impact`，在 `main.tex.j2` 里手写 `\section{Limitations} TODO` 即可。

---

## 完整目录结构

```text
<project>/.paic/templates/iclr2026/
├── main.tex.j2                  # 必需 — jinja 骨架
├── template.yaml                # 可选 — 元数据
├── sections/                    # 可选 — 覆盖某些 section
│   └── 02_related.tex.j2
├── iclr2026_conference.sty      # 静态：fill 自动复制到 drafts/
├── fancyhdr.sty
├── natbib.sty
└── figures/                     # 静态：fill 也会保留子目录结构
    └── logo.pdf
```

## 静态资源拷贝规则

| 文件 | 行为 |
|---|---|
| `*.sty` / `*.cls` / `*.bst` | ✓ 复制到 `drafts/` |
| `*.pdf` / `*.png` / `*.jpg` / `*.eps` | ✓ 复制到 `drafts/`（保留子目录） |
| `main.tex.j2` / `*.j2` / `*.jinja` | ✗ 不拷（jinja 模板，分别渲染） |
| `template.yaml` | ✗ 不拷（元数据） |
| `main.tex` | ⚠ 跳过（PAI-C 自己生成）；`static_assets_skipped` 会报 |
| `refs.bib` | ⚠ 跳过（PAI-C 从 `library/selected.yaml` 自己生成）；同上 |
| `sections/*.tex` | ⚠ 跳过（PAI-C 渲染）；同上 |

## 命名冲突

若给项目本地模板取名 `neurips`（与内置同名），fill 时**项目本地胜出**——`paic_draft_list_templates` 会标 `overrides_builtin: true`。这种"覆盖内置"是受支持的设计，常见于：你想给 `neurips` 加自家组的 logo / 样式微调。

## 提交进 git

`.paic/templates/<name>/` 跟随项目走。如果协作者也用 PAI-C，他们 clone 之后就直接能 `/paic-draft fill <name>`——零额外配置。

**版权注意**：venue 模板的 `.cls` 通常带版权（CTAN 上 LPPL 许可证、或 venue 自己的 license）。把 `.cls` 提交进 public repo 之前确认 license 允许。如果不能进 repo：

```gitignore
# .gitignore
.paic/templates/*/*.cls
.paic/templates/*/*.sty
.paic/templates/*/*.bst
```

只把 `main.tex.j2` 和 `template.yaml` 提交，协作者从 venue 官网自己下载 `.sty` 即可。

## 列出所有模板

```text
你: /paic-draft 列一下当前所有可用模板
Claude: → mcp__paic__paic_draft_list_templates(project_dir=<cwd>)

  [user]    iclr2026  — ICLR 2026 (Conference Track)  ★ 自带 .sty
  [user]    neurips   — Custom NeurIPS                 ★ overrides built-in
  [builtin] cvpr      — CVPR
  [builtin] ieee      — IEEE
```

## 故障

| 症状 | 排查 |
|---|---|
| `error: unknown_template` | 检查名字拼写；`paic_draft_list_templates` 看真实名 |
| 编译时报 `xxx.sty not found` | 你的模板里没拷 `.sty`，或在 main.tex.j2 里 `\usepackage{}` 写错了；检查 `.paic/templates/<name>/` 内容 |
| 渲染后 main.tex 空白 | 通常 `main.tex.j2` 用了未定义的 jinja 变量。`StrictUndefined` 模式会抛错；看 fill 工具响应里的 jinja 错误 |
| `error: template_already_exists` | scaffold 不会覆盖既有模板；先 `rm -rf .paic/templates/<name>` |
| 用户模板里的 `refs.bib` 被跳过 | 这是**故意的**——PAI-C 从 `library/selected.yaml` 自己生成 `refs.bib`。若希望用模板自带的，把 PAI-C 的 `refs.bib` 删掉再编译；但这破坏了 cite key 自动对齐 |

## 后续

- v0.2 (`/paic-draft polish`) 会读 `drafts/sections/*.tex` 做段落润色——和模板源无关，自定义模板照样吃。
- v0.3 (`/paic-draft compose`) 完整段落生成 + 引用对齐——同上。
- 全局模板路径 `~/.paic/templates/`（跨项目复用）目前不实现；若要复用，就在新项目里 `cp -r ../old-project/.paic/templates ./` 即可。
