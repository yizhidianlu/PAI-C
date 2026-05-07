---
name: paic-search
description: Multi-source paper search. Default flow covers arXiv (mcp__arxiv__*) + Semantic Scholar (mcp__paic__paic_s2_search). When the user has multi-platform search enabled in ~/.paic/config.yaml, also fans out to PubMed / bioRxiv / OpenAlex / Crossref / IEEE etc. via paper-search-mcp. Use when the user asks to "find papers about X", "search for related work on Y", or "扩充文献综述".
allowed-tools: mcp__paic__paic_workspace_status, mcp__paic__paic_search_strategy, mcp__paic__paic_search_pace, mcp__paic__paic_arxiv_pace, mcp__paic__paic_s2_search, mcp__paic__paic_dedupe, mcp__paic__paic_search_recall_check, mcp__arxiv__search_papers, mcp__arxiv__semantic_search, mcp__paper_search__search_pubmed, mcp__paper_search__search_biorxiv, mcp__paper_search__search_medrxiv, mcp__paper_search__search_pmc, mcp__paper_search__search_europepmc, mcp__paper_search__search_crossref, mcp__paper_search__search_openalex, mcp__paper_search__search_core, mcp__paper_search__search_dblp, mcp__paper_search__search_doaj, mcp__paper_search__search_openaire, mcp__paper_search__search_zenodo, mcp__paper_search__search_hal, mcp__paper_search__search_ssrn, mcp__paper_search__search_google_scholar, mcp__paper_search__search_iacr, mcp__paper_search__search_citeseerx, mcp__paper_search__search_base, mcp__paper_search__search_unpaywall, mcp__paper_search__search_ieee, mcp__paper_search__search_acm, mcp__paper_search__read_ieee_paper, mcp__paper_search__read_acm_paper
---

# /paic-search — multi-source paper retrieval

## When to use
- User says "找些关于 X 的论文" / "search papers about X" / "扩充 related work"
- User wants to compare what arXiv / Semantic Scholar (and optionally PubMed / bioRxiv / OpenAlex / IEEE / SSRN ...) each return for a topic

## What you do

1. **解析输入 + 生成检索词变体**

   1a. 看用户给的输入：

   - **specific query**（≥4 词，含具体方法名 / 数据集 / 年份 / 数字）→ 直接当主 query 用，跳到 step 2。一句话确认即可：「用 `<query>` 检索...」。
   - **broad topic**（1-3 词、纯概念、含中文、明显是话题如 "diffusion 视频" / "single cell"）→ 进入 1b 生成变体。

   1b. **生成 5-6 个英文检索词变体**，每个覆盖一个角度：

   | 角度 | 含义 | 例子（topic="diffusion video"） |
   |---|---|---|
   | 直接 | 主流英文表达 | `diffusion model video generation` |
   | 技术 | 具体方法 / 算法名 | `latent diffusion video synthesis` |
   | 应用 | 下游任务 / 数据集 | `text-to-video diffusion` |
   | 痛点 | 常见研究痛点 | `video diffusion temporal consistency` |
   | 近期 | 加近期标记 | `efficient video diffusion 2024` |
   | 邻近（可选） | 相关但更狭窄 / 更宽 | `controllable video generation` |

   生成时**只用主题里实际出现的概念 + 学科常识里的同义术语**。如果 topic 是你完全陌生的领域（罕见学科），少给几个变体（3-4 个）+ 标 "建议先选 #1"。

   1c. **渲染中文 numbered list 让用户选**：

   ```
   基于「<topic>」我准备了 6 个检索词变体（默认 #1）：
     1. <variant_1>          (直接)
     2. <variant_2>          (技术)
     3. <variant_3>          (应用)
     4. <variant_4>          (痛点)
     5. <variant_5>          (近期)
     6. <variant_6>          (邻近)
   想用哪几个？回车=#1；多选写 "1,3"；自定义 "custom: <你的词>"；
   混合 "1, custom: foo bar"。**上限 3 个**（超过 fan-out 时间会很长）。
   ```

   等用户回应后构造 `selected_queries` 列表：
   - 回车 / 没回应 → `[variants[0]]`
   - `"1,3,5"` → `[variants[0], variants[2], variants[4]]`
   - `"custom: foo bar"` → `["foo bar"]`
   - `"1, custom: bar"` → `[variants[0], "bar"]`
   - 选中 > 3 → 提示"3 个上限，要不要砍？"，砍后再继续

2. **Discover the active search strategy**:

   ```
   mcp__paic__paic_search_strategy(project_dir=<cwd>)
   ```

   Returns:
   ```
   {
     "enabled": bool,
     "domain_preset": str | null,
     "platforms": [str, ...],
     "pacing": {platform: float, ...},
     "max_results_per_platform": int,
     "warnings": [str, ...]
   }
   ```

   Print any `warnings` to the user (1 line each, Chinese summary), e.g. `跳过 ieee：缺 IEEE_API_KEY`.

3. **Branch on `strategy.enabled`** —— 对 `selected_queries` 中**每个** query 跑一遍下面的分支，把所有结果累积到 `all_results`：

   **(a) `enabled: false`** (the default — most users): legacy two-source flow.

   - Run **both** searches **in parallel** (single message, multiple tool calls):
     - `mcp__arxiv__search_papers(query=<this_query>, max_results=15, categories=[...])` — pick relevant arXiv categories from the topic (cs.LG / cs.CV / cs.CL / cs.AI / cs.MA / stat.ML / etc.)
     - `mcp__paic__paic_s2_search(query=<this_query>, limit=15, year_from=2022)` — for non-arXiv coverage; tighten `year_from` if the user asks for "recent" work

   **(b) `enabled: true`** (multi-platform fan-out): **Sequential, per-platform with pacing**.

   - Iterate the `strategy.platforms` list **in order, one platform per turn**. **Do NOT** put multiple platform searches in one message — voluntary pacing only works between turns.
   - For each platform, do exactly one search call followed by exactly one pace call:

     | Platform key | Search call | Pace call |
     |---|---|---|
     | `arxiv` | `mcp__arxiv__search_papers(query=<this_query>, max_results=strategy.max_results_per_platform, categories=[...])` | `mcp__paic__paic_arxiv_pace()` |
     | `semantic_scholar` | `mcp__paic__paic_s2_search(query=<this_query>, limit=strategy.max_results_per_platform)` | **skip** (S2 client is server-side rate-limited) |
     | `biorxiv` / `medrxiv` | **see 3.b.i below — query semantics不一样** | `mcp__paic__paic_search_pace(platform="<platform>")` |
     | anything else | `mcp__paper_search__search_<platform>(query=<this_query>, max_results=strategy.max_results_per_platform)` | `mcp__paic__paic_search_pace(platform="<platform>")` |

   - Note: paper-search-mcp tool names use underscores: `search_pubmed` / `search_biorxiv` / `search_google_scholar` / etc. (see allowed-tools list above for the full set).
   - If a platform call fails or returns empty, **continue to the next platform** — don't abort the whole search. Note the failure in a one-line warning at the end of step 6.

   **3.b.0 — per-platform query format（执行前必读）**

   不同平台对 query 形态的容忍度差异显著。把同一变体应用到不同平台前，按下表规整后再调：

   | 平台 | Query 形态 | 词数上限 | 处理方式 |
   |---|---|---|---|
   | `arxiv` / `openalex` / `semantic_scholar` / `europepmc` / `pmc` | 自然语言短语 + boolean 可 | 6-10 | 直传 |
   | `pubmed` | MeSH-friendly keyword | 5-8 | 直传 |
   | `crossref` | DOI 优先；title keyword 可 | 4-6 | 直传或截短 |
   | **`dblp`** | **纯 keyword（无 boolean、无引号短语）** | **≤3** | **LLM 截到最关键 3 词；过长返回空** |
   | `base` / `doaj` / `core` / `openaire` / `zenodo` / `hal` / `iacr` / `citeseerx` / `unpaywall` | broad topic | 3-5 | 截短到名词 |
   | `ssrn` / `ieee` / `acm` / `google_scholar` | natural language | 4-8 | 直传 |
   | `biorxiv` / `medrxiv` | 见 3.b.i（category 映射） | — | 不传 free-text |

   **dblp 实操**：长 query 如 `"graph attention transformer pretraining"` → LLM 取最关键 3 词 `"graph attention transformer"` 再调。dblp 是 author/title keyword index，不做语义检索；4+ 词几乎必返空。

   **3.b.i — biorxiv / medrxiv 的 query semantics（重要例外）**

   biorxiv/medrxiv 的上游 API（`api.biorxiv.org/details/{server}/{category}/{interval}`）**按 category + 时间窗过滤**，不是 free-text full-text search。直接把通用 query（如 `"motor imagery EEG channel selection"`）传进 `mcp__paper_search__search_biorxiv` 等于无效查询——上游会把 query 当 category 字符串去匹配，匹配不上就返回空 / 错误。

   走这两个平台时**先把 query 映射到 1-2 个 category**：

   | Query 主题关键词 | 推荐 biorxiv category | 推荐 medrxiv category |
   |---|---|---|
   | EEG / fMRI / brain / motor / neural / cognition | `neuroscience` | `neurology` 或 `psychiatry-and-clinical-psychology` |
   | gene / RNA / DNA / sequencing / transcriptom* | `genomics` 或 `genetics` | `genetic-and-genomic-medicine` |
   | sequence alignment / protein / methods / pipeline | `bioinformatics` | (无对应；skip medrxiv) |
   | cell / signaling / receptor / pathway | `cell-biology` 或 `molecular-biology` | (无；skip) |
   | cancer / tumor / oncolog* | `cancer-biology` | `oncology` |
   | virus / bacteria / pathogen / infection | `microbiology` 或 `immunology` | `infectious-diseases-(except-hivaids)` |
   | clinical trial / RCT / patient outcome | (skip biorxiv) | `clinical-trials` 或具体 specialty |
   | epidemiolog* / public health / surveillance | `epidemiology` | `epidemiology` 或 `public-and-global-health` |
   | drug / pharmacolog* / dosage | `pharmacology-and-toxicology` | `pharmacology-and-therapeutics` |
   | machine learning method 但非生物学主题 | (skip biorxiv) | (skip medrxiv) |

   实际调用：

   ```
   mcp__paper_search__search_biorxiv(query="<category-name>", max_results=...)
   ```

   `query` 字段填 category 名而非 free-text。返回的是**该 category 下最近 30 天**的预印本，再 client-side 按原 query 关键词在 title/abstract 里 substring 匹配筛选——召回有限但相关性高。

   **如果原 query 主题完全不在生物医学范畴**（如 "motor imagery EEG channel selection" 是 BCI/信号处理偏 CS-engineering，与 biorxiv 的 neuroscience category 只是边缘相关；或主题完全是 ML 理论 / 物理 / 经济），**直接 skip 该平台**：
   - **不要**调 `mcp__paper_search__search_biorxiv` 浪费 pace 配额
   - 仍然调一次 `mcp__paic__paic_search_pace(platform="biorxiv", seconds=0)`（或省略），但在最终 warning 里加一行：「跳过 biorxiv：query 不在 biorxiv 类目（生命科学）覆盖范围」
   - medrxiv 同理（医学/临床覆盖；非临床主题直接 skip）

   **遇到歧义**（既能是 ML 也能是 neuroscience，如本例 motor imagery EEG）：
   - 倾向 **map 到最相关 category 跑一次**（neuroscience），返回空也比不跑信息量高
   - 在最终表格的 "命中 query" 列旁加一行小字说明命中的是 neuroscience 30 天窗口，让用户自己判断要不要 ingest

   **多 query 间**：每跑完一个 query 的 fan-out → 进下一个 query 前不需要额外 pacing（每次平台 call 后已经 pace 过），但**不要并发**多个 query。

4. Tag each returned paper with `platform` (the upstream key) and `source = "external"` for paper-search-mcp results, or `source = "arxiv"` / `source = "s2"` for the legacy paths. Add `matched_query=<this_query>` so dedupe 后能显示哪个变体命中。Preserve any `doi` / `pmid` / `external_ids` the upstream returned.

5. **跨 query + 跨 platform 合并** → `all_results`。每篇论文加 `matched_query=<query>`。

6. Call `mcp__paic__paic_dedupe(papers=all_results)`. 一次 dedupe 同时跨 query 跨 platform。dedupe 合并 duplicate group 时，把多个 `matched_query` 合成 list（如 `["#1", "#3"]`）。DOI / arxiv_id / s2_id / fuzzy title 已经覆盖大多数重叠情形。

7. **强制召回校验（Step 6.5 — 不可跳过）**

   dedupe 后**必须**跑这一步再渲染表格。目的：避免标题里明确含核心检索词的论文被注意力筛掉。

   7a. 从 `<原始 topic> + <selected_queries>` 提取 **2-4 个 `core_terms`**（英文短语）：
   - 中文 topic 先翻成主流英文术语（如 "脑机接口" → `brain-computer interface` 或 `BCI`）
   - 保留：实词名词短语 / 方法名 / 数据集名 / 缩略词（如 `motor imagery` / `EEG` / `channel selection`）
   - **剔除**通用词：`method` / `model` / `approach` / `based on` / `using` / `survey` / `framework` / `efficient` / `2024`
   - 多词短语保留为单个 term（用空格分隔），不要拆词

   7b. 中文一行印出 core_terms 让用户**可选**修正（无回应直接继续）：
   ```
   核心词（自动提取，title 强制召回用）: ["motor imagery", "EEG", "channel selection"]
   要修改回 "core_terms: A, B, C"，否则继续。
   ```

   7c. 调 `mcp__paic__paic_search_recall_check(papers=<deduped.unique>, core_terms=<final_list>)`，拿到四桶 idx 列表 + 每篇 `hits`。
   - 默认 `match_mode="loose"`、`tier1_min=5`，正常无需覆盖
   - 用户给的 core_terms ≤ 2 个时，loose 自动等价 strict（工具内部处理）

8. Optional: if the user already has papers in their library and asks "扩展现有文献", also call `mcp__arxiv__semantic_search(query=<#1>)` against the local index for tighter relevance.

9. Render a Chinese-headed markdown **分两段**表格——**严格按 step 7c 的桶分配**，不要凭注意力重排：

   **段 A · 强相关·标题命中核心词**（合并 `tier1_strict` + `tier1_loose`，前者在前）：
   ```
   ## 强相关 · 标题命中核心词（{N1} 篇）
   | 序号 | 标题 | 作者 | 年份 | 来源 | id | 命中词 |
   ```

   **段 B · 其他召回·部分命中或仅 abstract 相关**（合并 `tier2_partial` + `tier3_others`）：
   ```
   ## 其他召回（{N2} 篇）
   | 序号 | 标题 | 作者 | 年份 | 来源 | id | 命中词 |
   ```

   两表共用列约定：
   - 序号 / 标题 / 作者(前两位) / 年份 / venue / 来源 / id
   - **来源** column: shows `arxiv` / `s2` / `pubmed` / `biorxiv` / `openalex` / etc. (use `platform` if set, else `source`).
   - **命中词** column：渲染该篇 `hits` 字段（如 `motor imagery, EEG`）；空 = 段 B 才出现的零命中
   - **命中 query** column（仅当 `len(selected_queries) > 1` 时增加）：列出该篇被哪些 query 命中（如 `#1, #3` 或 `#1, custom`）
   - id 列 prefer arxiv_id, then doi, then s2_id, then external_ids (e.g. PMID)
   - 用 `*cached*` 后缀标注 from_cache 命中

   段 A **每一篇**必须出现，不可省略。段 B 可在 N2 ≥ 30 时折叠保留前 30（按命中数降序），并加一行 "其他 K 篇 0 命中已折叠"。

10. End with a one-line Chinese suggestion: "如要纳入项目，运行 `/paic-ingest <序号或id>`"。
   - 如果用了多个 query，加一行小结："本次 #1 命中 N 篇、#3 命中 M 篇，去重后 K 篇唯一"。
   - If multi-platform mode contributed unique papers from new sources, a brief sentence highlighting which platforms helped (e.g. "本次 PubMed 贡献了 4 篇 arXiv 上没有的临床研究") is welcome but optional.

## Style
- 中文叙述。表格里的 title/author 保留原文。
- 不要预先评价论文优劣——把判断留给用户。
- 命中重复组时，用一行注脚说明 "去重合并: [(idx1,idx2), ...]"。
- **Legacy 模式（strategy.enabled=false）**：arxiv + s2 可以单消息并发——这是历史既定行为。
- **多平台模式（strategy.enabled=true）**：每个平台一次消息，平台调用后**必须**紧跟一次 `paic_search_pace`（arxiv 用 `paic_arxiv_pace`）。漏调 pace 会撞上游限流。
- 不要把同一查询拆成多条 `mcp__arxiv__search_papers` 并发——arxiv.org 限流默认 1 req/3s，并发会触发 429。
- **arxiv 连续失败 fallback**：单次 `/paic-search` 内 arxiv 对同一 query 连续 ≥2 次抛 SSL / 429 / timeout → 立刻停掉本次 arxiv 调用，剩余 query 不再回 arxiv（视为本次会话冷却）。改用 `openalex` + `semantic_scholar` + `crossref` 三方去重交集补全。**避免**陷入"5 次重试全失败"的死循环。最终 warning 加一行"arxiv 本次跳过：连续限流"。
- 如果 `paic_search_strategy` 返回 `warnings`（典型：`ieee skipped: IEEE_API_KEY not set`），把每条 warning 以一行中文提示带在最终表格之前；不需要重复说明用户怎么修复（`docs/external-search.md` 有详细教程）。
- **变体生成纪律**：只用主题里出现的概念 + 学科常识里的同义术语，**不要凭空乱编**。冷门 / 你没把握的领域：少给变体（3-4 个）+ 标 "建议先选 #1"。
- **specific query 跳过 variant**：用户给 4+ 词 + 含具体方法 / 数据集 / 年份的输入 → 直接跑搜索，不要硬塞 variant 步骤增加摩擦。

## 常见情况
- 用户说"我之前装过 paper-search-mcp 但好像没用"——大概率是 `providers.external_search.enabled=false`。让他跑 `uv run paic doctor` 看那行；要开启就改 yaml 然后**完全重启** Claude Code。
- 用户说"找点 PubMed 的"但 strategy 返回 enabled=false——直接告诉他先开 multi-platform；不要自作主张试着调 `mcp__paper_search__*`（即使你看到这些工具在 allowed-tools 里）。
- **biorxiv/medrxiv 返回空**：检查 query 是不是直接当 category 传进去了（见 step 3.b.i）。如果 query 是 free-text full-text 风格（如 `"motor imagery EEG channel selection"`），上游 30 天 category 窗口 + client-side substring 匹配很难命中——这是上游 API 限制，不是 PAI-C 配置错。把 query 映射到 category 重试，或在该平台 skip。

## 已知陷阱
- **biorxiv/medrxiv 的 query 语义错配**：上游 `api.biorxiv.org/details/` endpoint **不是 full-text search**——只按 category + 30 天时间窗过滤。把通用 query 传进去 = 召回近零。Step 3.b.i 强制 category 映射或 skip，避免无谓 fan-out。其它 paper-search-mcp 平台（pubmed / openalex / crossref / europepmc）都是真正的 full-text search，query 直传即可。
- **dedupe 之后凭注意力筛 paper**：把 `paic_dedupe.unique` 直接交给 LLM 渲染表格 → 召回池超过 ~50 篇时，LLM 倾向悄悄略过尾部；标题里明确含核心检索词的论文也会因为不在 attention 焦点内被漏掉。**Step 7（强制召回校验）不可跳过**：必须调 `paic_search_recall_check`，按返回的 4 桶 idx 渲染分两段表格。SKILL 的纪律不能保证 → 工具的确定性返回值才能保证。
- **biorxiv 的近期窗口偏窄**：上游默认只看最近 30 天预印本。要找历史工作（>30 天前）biorxiv 是错的工具——用 pubmed / europepmc / openalex 走 DOI 索引。
- **同主题在 biorxiv 和 medrxiv 都跑一遍意义有限**：biorxiv = 基础生命科学预印本；medrxiv = 临床医学预印本。EEG / fMRI 偏 neuroscience 走 biorxiv；RCT / 临床流行病走 medrxiv。混跑只是徒增 fan-out 时间。
