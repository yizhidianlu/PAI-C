# 完整工作流：9 步从空目录到 LaTeX 骨架

<p align="center">
  <img src="paic-workflow.png" alt="PAI-C workflow" width="900">
</p>

> Slash command 使用连字符：`/paic-search` ✓ ；`/paic:search` ✗ 。

首次使用先完成 [getting-started.md](getting-started.md)。本文假定 `paic doctor` 全绿。

每步给出：**调用方式** + **`.paic/` 产出位置** + **典型耗时** + **路由 node**。

---

## 1. `/paic-init` — 建项目

```
/paic-init 方向：long-context attention 在医学影像报告生成；目标 NeurIPS 2027。
```

- **产出**：`.paic/project.yaml`（含 title / venue / deadline / related_topics）
- **耗时**：< 1 秒
- **路由**：纯本地 fs，不调 LLM

> Skill 要求当前目录为空或仅含 `.paic/`，避免在已有项目误初始化。

---

## 2. `/paic-search <query>` — 多源检索

```
/paic-search 近两年 long-context transformer 在长文本摘要 / 医学报告生成的代表性工作。
```

- **来源**：
  - `mcp__arxiv__search_papers` + `mcp__arxiv__semantic_search`（arXiv）
  - `mcp__paic__paic_s2_search`（Semantic Scholar；客户端 0.95 req/s 限速）
- **去重**：DOI / arxiv_id 主键 + 标题 fuzzy（threshold 0.92）
- **section-aware retrieval**（compose 阶段）：当 library > 40 篇时，`/paic-draft compose` 自动用 BM25 + MMR 按 section 检索 top-40 而非按 selected.yaml 顺序截断；query 由 paper_plan 的 thesis / 当前 section 的 intent / idea / experiment 自动拼装。直接调 `paic_library_retrieve` 也能拿到 ranked hits + match_reason。
- **claim ledger**：每次 compose 跑完自动把段落里的 strong claim（novelty / comparative / numeric / result）抽出来落到 `.paic/plans/claims.yaml`。`/paic-draft compose` 输出末尾会列出 needs_evidence 的强声明。`paic_claims_validate` 跨 claim 检查 cite_key / experiment_id / 强声明无支持的问题。
- **experiment plan 验证切面**（phase 5）：`/paic-experiment` 跑完后插入一个程序化 `verify_plan` 节点，检查 baseline 是否带 paper_ref / dataset 是否带 license + splits / metric 是否恰好一个 primary / ablation 每轴 ≥2 levels / compute_budget / statistical_plan / reproducibility 都填了。Soft warnings 落 `experiment.yaml` 的 `validation_warnings`，SKILL 渲染时一行一条提示。
- **paragraph-level compose**（phase 6，opt-in `mode="paragraph"`）：把 section 的生成拆成 outline → write per paragraph → coherence polish 三步。比 `from_stub` 慢（N+2 次 LLM call）但长 section 重复短语少、各段绑 claim_ids + cite_key 候选、轨迹可追。强烈建议在 paper_plan + claims 齐全时跑长 section（intro / related / experiments）。
- **强制召回校验**（dedupe 之后、渲染表格之前）：Skill 从 topic + 选用的 query 变体里抽 2-4 个 `core_terms`（英文短语），调 `paic_search_recall_check` 把召回池按 title 命中数分四桶（`tier1_strict` / `tier1_loose` / `tier2_partial` / `tier3_others`）。最终表格分两段渲染——「强相关·标题命中核心词」和「其他召回」——避免长召回池里标题明确含核心词的论文被注意力筛掉。`tier1_min=5` 兜底：T1 不足时从 T2 按命中数降序补到下限。
- **产出**：返回候选表（不落盘；`/paic-ingest` 才入库）
- **耗时**：5–15 秒，取决于 query 复杂度
- **缓存**：S2 响应缓存到 `~/.paic/cache/s2/<sha>.json`，永不过期；绕过缓存传 `force=true`

### 检索词变体

输入为 **broad topic**（1–3 词、纯概念词、含中文）时，Skill 先生成 5–6 个英文检索词变体供选择：

```
基于「diffusion video」候选 6 个检索词变体（默认 #1）：
  1. diffusion model video generation       (直接)
  2. latent diffusion video synthesis       (技术)
  3. text-to-video diffusion                  (应用)
  4. video diffusion temporal consistency   (痛点)
  5. efficient video diffusion 2024          (近期)
  6. controllable video generation diffusion (邻近)
请选择：回车 = #1；多选 "1,3"；自定义 "custom: foo bar"
```

回车走默认 #1。多选最多 3 个，超过会拖慢 fan-out（3 query × 5 platform × pacing ≈ 22s）。

输入为 **specific query**（4+ 词且含具体方法 / 数据集 / 年份）时，Skill 跳过 variant 步骤，直接作主 query。

> 未设置 S2 key 时自动降到 0.33 req/s 匿名档，多源搜索仍可执行。

---

## 3. `/paic-ingest <ids>` — 入库 + 下载

```
ingest 第 1, 3, 7 篇。
```

- **元数据**：写入 `.paic/library/selected.yaml`（PaperRef）
- **本地存档**：`.paic/library/pdfs/<cite_key>.<ext>`。`<cite_key>` 与 BibTeX 键一致（`arxiv_2401_12345` / `doi_10_1234_abc`）；扩展名为 `.md`（arxiv markdown）或 `.pdf`（其他平台）
- **平台路由**：
  - **arXiv**：`mcp__arxiv__download_paper` 写到上游 storage（`~/Documents/arxiv-papers/` 等），随后 `paic_library_attach_paper` 复制 markdown 到项目本地
  - **paper-search-mcp 平台**（pubmed / biorxiv / medrxiv / pmc / openalex / crossref，需 `external_search.enabled=true`）：`mcp__paper_search__download_<platform>(save_path=<project>/.paic/library/pdfs/<cite_key>.pdf)` 直接落地
  - **DOI-only**：`mcp__paper_search__download_with_fallback`（Unpaywall / Crossref 等）
  - **不可下载**（s2 / google_scholar / ssrn / iacr）：跳过下载、仅入库元数据；摘要标注 `未下载: N 篇（原因）`
- **去重**：再次按 arxiv_id / DOI 与已有 `selected.yaml` 校验，重复条目计入 `skipped_duplicates`
- **节流**：arxiv 默认 6s pace；paper-search-mcp 各平台用 `paic_search_pace(platform=...)`，per-platform 配置见 `~/.paic/config.yaml`
- **429 重试**：自动 `paic_*_pace(seconds=20)` 后重试；连续 3 次 429 时由用户决定 skip 或调高 pace
- **耗时**：每篇 5–30 秒（网络 + 转换）；17 篇 arXiv 默认 ~102s

---

## 4. `/paic-summarize [id|all]` — 结构化摘要

```
/paic-summarize all
```

- **读取（4 级 fallback）**：(1) `paper_text=` 旁路；(2) 上游 arxiv markdown（仅 arxiv）；(3) `<project>/.paic/library/pdfs/<cite_key>.md`（ingest 阶段 attach 拷贝）；(4) `<project>/.paic/library/pdfs/<cite_key>.pdf` + pypdf 提取（命中 `~/.paic/cache/pdf_text/<sha>.txt` 永久缓存）。响应 `text_source` 字段表示生效路径
- **平台覆盖**：arXiv 走 (2)；PubMed / bioRxiv / OpenAlex / Crossref 等走 (4)
- **写入**：`.paic/library/summaries/<paper_id>.md`
- **结构**：problem / method / key_results / limitations / techniques / relevance_to_project
- **路由 node**：`summarize`（按 `routing.default` 或 `routing.overrides.summarize`）
- **耗时**：每篇 30–60 秒（API / SDK 模式）；host 模式由主对话生成，延迟与当前会话一致；首次 PDF 提取额外 ~0.5s/篇（之后命中缓存）
- **PDF 失败模式**：响应中 `pdf_extraction_failed_reason` 给出原因——`encrypted`（用 `qpdf --decrypt`）/ `empty_extraction`（扫描版，建议 `ocrmypdf`）/ `corrupt`（重新 ingest）
- **退路**：4 级 fallback 全失败时，调 `mcp__arxiv__read_paper` 取文本传入 `paper_text=` 参数
- **Host orchestration**：`routing.overrides.summarize: host` 时 PAI-C 不发 LLM 调用——`paic_summarize_run` 返回 `mode: host_orchestration` + 论文 markdown + JSON schema，Skill 让主对话生成结构化字段后调 `paic_summarize_persist` 写盘。详见 [configuration.md → Host Orchestration](configuration.md#host-orchestration订阅复用零外部-llm-调用)

---

## 5. `/paic-ideate [focus]` — 生成 idea（含用户筛选）

```
/paic-ideate focus 在 "如何不堆 KV cache 扩到 200k token"，给出 8 个 idea。
```

工作机制：

1. 后台启动 LangGraph run（fire-and-poll），状态写入 `.paic/state/checkpoints.sqlite`
2. 头脑风暴节点拉取所有 `summaries/*.md` 作为 grounding，产生 N 张候选 IdeaCard
3. **暂停**等待用户筛选——Claude 列出预览卡询问保留哪几条
4. 用户回复 "保留 1, 4, 7"，graph 继续，写入 `.paic/ideas/<idea_id>.yaml`

- **路由 node**：`ideate_brainstorm` + `idea_score_{methodology,novelty,impact,reviewer2}`（4-persona panel）
- **跨会话恢复**：暂停在用户筛选这步，关闭 Claude Code 后下次 `/paic-resume` 续跑
- **耗时**：脑暴 1–3 分钟；筛选后 finalize ~10 秒

> IdeaCard 含 `feasibility / novelty / impact` 三维评分 + `composite_score`。

---

## 6. `/paic-experiment <idea_id>` — 实验方案

```
/paic-experiment 选评分最高的 idea，约束：单张 A6000、3 周内完成。
```

- **写入**：`.paic/experiments/<exp_id>.yaml`
- **结构**：research_questions / hypotheses / datasets / baselines / proposed_method（含伪代码）/ metrics / ablations / compute_budget / success_criteria / threats_to_validity / timeline_weeks
- **路由 node**：`experiment_design`
- **耗时**：1–2 分钟，无 interrupt

---

## 7. `/paic-review <experiment_id>` — 4-persona 多轮评审

```
/paic-review 跑 2 轮 4-persona 评审。
```

最长的一步——4 persona × N 轮 + moderator + verdict。

| 角色 | 职责 | node 标签 |
|---|---|---|
| methodology | 方法论严谨性 | `review_persona_methodology` |
| statistics | 统计 + 数据 + reproducibility | `review_persona_statistics` |
| domain | 领域 fit 与文献定位 | `review_persona_domain` |
| reviewer2 | 创新性 + 红队视角 | `review_persona_reviewer2` |
| moderator | 综合 4 个 critique 出 issue list | `review_moderator` |
| verdict | 终判 accept/major/minor/reject | `review_verdict` |

工作机制：

1. `retrieve_context` 拉取 5–10 篇相关 summary 进入上下文
2. 每轮：4 persona 串行 critique → moderator 综合 → **暂停等用户 rebuttal**
3. 用户提交 rebuttal（"针对 issue #1 加入 LongBench baseline；issue #2 同意将 seed 提到 5"）
4. `apply_rebuttal` 更新 experiment / rebuttals → `decide_next_round`
5. 跑完 `max_rounds` 或 issue 全部 resolved → `verdict`

- **产出**：`.paic/reviews/<exp_id>/{transcript.yaml, round_1.md, ..., verdict.yaml}`
- **耗时**：每轮 4–8 分钟（4 persona × 1–2 分钟）。graph state 落盘，关闭 Claude Code 不影响恢复

> 不同 persona 路由到不同 LLM（如 reviewer2 用 Opus、其余用便宜模型）：见 [configuration.md → 路由 overrides](configuration.md#路由-routing)。

---

## 8. `/paic-draft <stage>` — LaTeX 三阶写作

### 8.1 fill — 模板填充（v0.1）

```
/paic-draft fill 用 NeurIPS 模板，基于刚 verdict 的 idea + experiment 起骨架。
```

- **模板**：内置 `cvpr` / `neurips` / `ieee`（jinja2 模板在 `src/paic/latex/templates/`）；项目本地自定义模板在 `<project>/.paic/templates/<name>/`，详见 [custom-templates.md](custom-templates.md)
- **写入**：`.paic/drafts/main.tex` + `.paic/drafts/sections/0X_*.tex` + `refs.bib` + 模板自带的 `.sty / .cls / .bst / figures` 自动拷贝
- **填充范围**：title / abstract（一句话 motivation）/ introduction（contributions 列表）/ method 框架 / experiment setup / 占位 results 段
- **scaffold 新 venue**：`paic_draft_scaffold(name="iclr2026", base="neurips")` 派生起点，再替换 `\usepackage{...}` 与拖入 venue `.sty`
- **不做**：v0.1 不跑编译、不写完整段落（v0.2 polish + v0.3 compose 负责）
- **耗时**：5–15 秒（纯模板渲染，不调 LLM）

### 8.2 polish — 段落级 LLM 重写（v0.2）

```
/paic-draft polish 01_intro --mode tighten
/paic-draft polish 03_method --mode expand     # 把 fill 留下的 TODO 占位扩写完整
/paic-draft polish 02_related --mode formalize --instruction "用第三人称"
```

- **5 模式**：`tighten` / `clarify` / `formalize` / `expand` / `proofread`；可附 `instruction` 自由指令
- **写盘 + 备份**：覆盖原文件，旁置 `<section>.tex.bak.<UTC>`
- **结构 guard**：保留所有 `\cite{}` 键、不引入新 cite key、`\begin/\end` 配对、大括号配对——任一未通过返回 `latex_validation_failed` 不写盘
- **expand 模式**：要求 `idea_id`（或 Skill 自动从最新 idea 推断），将 idea / experiment yaml 注入 prompt
- **dry_run**：`--dry-run` 仅输出 diff
- **路由 node**：`draft_polish`；支持 `routing.overrides.draft_polish: host`

### 8.3 compose — 整段生成 + 引用对齐（v0.3）

```
/paic-draft compose 02_related --mode from_stub
/paic-draft compose 01_intro --mode from_scratch --target-words 800
```

- **2 模式**：`from_stub`（默认，将当前文件作为 outline 扩写）、`from_scratch`（从 idea + library 重新生成，忽略当前文件）
- **引用对齐**：将 `library/selected.yaml`（cap 40 篇）每篇的 `[cite_key] title (author, year) — 一句概括` 注入 prompt，LLM 仅从该 whitelist 选择 cite。**所有** `\cite{KEY}` 必须存在于 library，否则返回 `latex_validation_failed` 不写盘
- **section-aware 长度建议**：intro 800 / related 700 / method 900 / experiments 800 / conclusion 220 / abstract 200（用户传 `target_words` 覆盖）
- **写盘 + 备份**：与 polish 同
- **错误恢复**：guard 列出 `cite_keys_missing_from_library`——ingest 缺失论文后重试，或换 mode
- **路由 node**：`draft_compose`；支持 `routing.overrides.draft_compose: host`

**典型流程**：fill → compose（写出含真实引用的段落）→ polish（细调措辞）。

> 里程碑见 [README → Status](../README.md#status)。

---

## 9. `/paic-figure <stage>` — 论文配图（Phase 1 raster）

启用前置：`providers.images.enabled: true`，详见 [configuration.md → images](configuration.md#images-paic-figure)。

### 9.1 plan — 提议图位

```
/paic-figure plan
```

- **读取**：传入 `draft_path=` 时优先读 polished `.tex`；否则回退到 `idea + experiment` YAML
- **产出**：`.paic/figures/_plan.yaml`，含 ≤4 个 slot（`teaser` / `concept` / `domain`），每条带 `section_hint` / `position_hint` / `scene_description` / `caption_hint` / `rationale`
- **路由 node**：`figure_plan`
- **耗时**：10–20 秒（1 次 LLM 调用，无图像 API 成本）

### 9.2 generate — 渲染图片

```
/paic-figure generate teaser
```

- **流程**：合成 academic-style 图像 prompt（节点 `figure_prompt`）→ 调用图像 API → 写入 `.paic/figures/<slot>/v1.png` + `meta.yaml`
- **返回**：`png_path` + `image_prompt` + 即用的 `\begin{figure}...\includegraphics{figures/<slot>/v1.png}...\end{figure}` 片段
- **耗时**：10–30 秒/张（视后端）；gpt-image-1 在 `quality=high` 下约 ¥0.5–2/张（中转站）

### 9.3 edit / variant — 改图

```
/paic-figure edit teaser "调暗整体色调，增加海洋元素"
/paic-figure variant teaser n=2
```

- `edit` 基于最新版微调，写入 `v<n>_edit.png`，meta 记录 `parent_version`
- `variant` 基于最新版生成 N 个备选，写入 `v<n>_variant.png`
- gpt-image-1 无 native variations，`variant` 走 `edit` + "alternative variation" 提示模拟
- 多次 edit 会偏移；建议每 slot 至多 edit 2 次，不满意则修改 `scene_description` 重新 generate

### 范围限制

- **不适合**：架构图（boxes + arrows + 标签）、定量结果图（曲线 / 柱状图）、含精确文字的图表
- **替代方案**：架构图用 TikZ；结果图用 matplotlib + 真实实验数据

---

## 跨会话恢复

下次启动 Claude Code，第一句：

```
/paic-resume
```

列出 paused / awaiting_input 的 run。继续指定 run：

```
/paic-resume 01HX8K... rebuttal："针对 issue #1，..."
```

底层调用 `mcp__paic__paic_runs_resume`，从 `.paic/state/checkpoints.sqlite` 取 thread state 续跑。

---

## 状态查看

```
/paic-status
```

输出：项目元信息、library / ideas / experiments / reviews 计数、当前活跃 LangGraph run。

---

## 端到端串接

```
方向：transformer 在脑电信号自动诊断。请按 init → search → ingest → summarize
→ ideate → experiment → review → draft fill → figure plan 串起来，每个用户 checkpoint 暂停等我。
```

Claude 自行按顺序串接 skill。
