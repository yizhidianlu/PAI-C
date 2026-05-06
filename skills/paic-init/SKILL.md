---
name: paic-init
description: Initialize a PAI-C project (.paic/ directory) in the current working directory. Use this once at the start of a new paper project before any other /paic-* commands.
allowed-tools: Bash, Read, mcp__paic__paic_workspace_init, mcp__paic__paic_workspace_status, mcp__paic__paic_search_strategy
---

# /paic-init — initialize a PAI-C project

## When to use
- The user is starting a new paper and runs `/paic-init` (optionally with a title).
- `/paic-status` reports `initialized: false` and the user wants to start.

## What you do

> **Critical**: Always run **step 2 before step 3**. The strategy check must precede `paic_workspace_init` so the domain interview happens *before* `project.yaml` gets written. Do NOT skip step 2 even if the user only typed `/paic-init` with no args.

1. Confirm the working directory with the user (one short Chinese sentence). Default to the current cwd.

2. **Always call `mcp__paic__paic_search_strategy(project_dir=<cwd>)` first** — even on a brand-new uninitialized directory (the tool tolerates that). The response tells you whether multi-platform search is globally enabled. Also call `mcp__paic__paic_workspace_status(project_dir=<cwd>)` to learn whether this project already exists and (if so) what `domain_preset` it has.

3. **Branch on `strategy.enabled`**:

   **(a) `enabled: false`** (the default — most users): no interview.
   - Call `mcp__paic__paic_workspace_init` with `project_dir`, plus `title` / `venue` / `deadline` only if the user provided them.
   - Jump to step 4.

   **(b) `enabled: true`** (multi-platform mode is on): you **MUST** confirm the domain with the user before writing `project.yaml`.

   - **If `workspace_status` showed `initialized: false`** (brand-new project) — run the **6-preset interview（支持多选取并集）**:

     Ask in one Chinese sentence which research domain best fits this project, listing the presets and the multi-select syntax:

     ```
     1. cs_ml — 计算机 / AI / ML / NLP / CV / 统计
     2. biomed — 生物医学 / 临床 / 生命科学
     3. physics_math — 物理 / 数学 / 天文
     4. econ_social — 经济 / 金融 / 管理 / 社会学
     5. engineering — 工程 / 电子 / 机械 / 材料
     6. interdisciplinary — 跨学科 / 综合（默认）
     7. 自定义（手写 platforms 列表）

     单选输入数字（如 `1`）；多选用逗号分隔取并集（如 `1,2,5` = cs_ml + biomed + engineering）。
     交叉学科主题（如 BCI / bioinformatics / digital health / quantum chemistry / climate-AI）建议多选。
     ```

     Wait for the user's answer. If the user only types `/paic-init <title>` and stops, **ask the question and pause** — don't fill it in yourself.

     **解析答案：**

     | 输入 | 处理 |
     |---|---|
     | 单数字 `1`-`6` | 单 preset；调 `workspace_init(domain_preset=<name>)` |
     | 多数字 `1,2,5` 或 `1, 2, 5` | 取 union；见下方"多选取并集" |
     | `6` 或回车 | `interdisciplinary`（默认） |
     | `7` / `自定义` | 追问 "请列出平台名（如 `pubmed, biorxiv, openalex`）"，参考 `docs/external-search.md` |

     **多选取并集：**

     当用户输入 ≥2 个数字时（`1,2,5`），按下表取每个 preset 的 platforms 并集，再调 `workspace_init(platforms_override=<union list>)`（**不传 `domain_preset`**——底层会 tag 为 `"custom"`）：

     | Preset | Platforms |
     |---|---|
     | cs_ml | arxiv, semantic_scholar, openalex, dblp, acm |
     | biomed | pubmed, biorxiv, medrxiv, europepmc, pmc, semantic_scholar |
     | physics_math | arxiv, semantic_scholar, openalex |
     | econ_social | ssrn, openalex, semantic_scholar, crossref |
     | engineering | arxiv, openalex, crossref, ieee, acm |
     | interdisciplinary | arxiv, semantic_scholar, openalex, crossref, doaj |

     去重并保持首次出现顺序。底层 `_resolve_platforms` 会自动前置 `arxiv` + `semantic_scholar`，但你传 union 列表时**不需要手动加**（重复会被去重）。

     **示例**：用户选 `1,2,5` →
     - cs_ml = `[arxiv, semantic_scholar, openalex, dblp, acm]`
     - biomed = `[pubmed, biorxiv, medrxiv, europepmc, pmc, semantic_scholar]`
     - engineering = `[arxiv, openalex, crossref, ieee, acm]`
     - union（保序去重）= `[arxiv, semantic_scholar, openalex, dblp, acm, pubmed, biorxiv, medrxiv, europepmc, pmc, crossref, ieee]`
     - 调 `workspace_init(project_dir=..., platforms_override=[...12 个...])`

   - **If `workspace_status` showed `initialized: true` AND `domain_preset` is already set** (re-init scenario) — surface the current value and offer a swap:

     > "项目已设置 `domain_preset: <current>`（platforms: `[…]`）。要换吗？回车保持原样，或输入新 preset / 新平台清单。"

     If user presses enter / answers "保持" / "no" — keep the existing preset (don't pass `domain_preset` to `workspace_init`). If user picks a new preset or list, run the interview branch above.

   - **If `workspace_status` showed `initialized: true` but no `domain_preset`** (legacy project predating multi-platform search) — same 6-preset interview as the brand-new case.

   - Once you have a preset (or platforms list), call `mcp__paic__paic_workspace_init` **once** with:
     - `project_dir`, `title` / `venue` / `deadline` (the latter three only if user provided)
     - **单选**：传 `domain_preset` = `cs_ml` / `biomed` / `physics_math` / `econ_social` / `engineering` / `interdisciplinary`，**不传** `platforms_override`
     - **多选取并集** 或 **自定义**：传 `platforms_override` = union list（多选）或用户清单（自定义），**不传** `domain_preset`（底层自动 tag 为 `"custom"`）

4. Call `mcp__paic__paic_workspace_status` to confirm the final state.

5. Render a 1-line Chinese confirmation, then a short tree of the `.paic/` skeleton (`library/`, `ideas/`, `experiments/`, `reviews/`, `drafts/`, `state/`). When the project recorded `platforms`, also list them on a single line below the tree.

## Style
- Conversation in Chinese. All file content (paths, project_id) shown verbatim.
- Do not invent a title for the user; leave blank if they did not say one.
- **Be quiet about confirmations** — no extra commentary on success cases. **But always run the domain interview when `strategy.enabled=true`**; that question is mandatory, not optional. If `enabled=false` you stay quiet completely (no interview). The "be quiet" rule never overrides the interview.
- During the domain interview keep it short: list the presets, ask once, accept the answer. Don't lecture about each preset's coverage — `docs/external-search.md` exists for that.
- When the init reports `created: false`, mention it was already initialized (and what got patched, e.g. "更新 domain_preset → biomed").
