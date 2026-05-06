---
name: paic-summarize
description: Generate structured summaries (problem / method / key results / limitations / techniques) for papers in the project library. Use after /paic-ingest when you want a quick read of new papers without manually opening each PDF.
allowed-tools: mcp__arxiv__download_paper, mcp__arxiv__list_papers, mcp__arxiv__read_paper, mcp__paic__paic_summarize_run, mcp__paic__paic_summarize_persist, mcp__paic__paic_workspace_status, mcp__paic__paic_arxiv_pace
---

# /paic-summarize — structured paper summaries

## When to use
- After `/paic-ingest`, the user says "总结一下" or "summarize them"
- User wants to revisit a paper they ingested earlier without rereading the PDF

## What you do

1. Resolve the target paper id(s):
   - If user passed an explicit id (e.g. `/paic-summarize 2401.12345`), use it.
   - If user said "all" or "全部", call `mcp__paic__paic_workspace_status` to learn how many papers are in the library, then iterate.
   - If user gave a fuzzy reference ("那篇 video diffusion 的"), match it from the latest `/paic-search` or `/paic-ingest` tool result.

2. Per paper, call `mcp__paic__paic_summarize_run(project_dir=<cwd>, paper_id=<id>)`.

   **全文解析路径**（透明给你，由 PAI-C 自动按顺序尝试；响应里的 `text_source` 字段告诉你走了哪条）：
   1. `caller_supplied` —— 你显式传的 `paper_text=`
   2. `local_markdown` —— 上游 arxiv MCP 的 markdown（仅 arxiv 论文）
   3. `library_pdfs_md` —— `<project>/.paic/library/pdfs/<cite_key>.md`（ingest 时 attach 拷贝的）
   4. `library_pdfs_pdf_extracted` —— `<project>/.paic/library/pdfs/<cite_key>.pdf` + pypdf 提取（命中 `~/.paic/cache/pdf_text/<sha>.txt` 永久缓存）

   **绝大多数情况下你不用管解析路径**——summarize 自己解决。

   **当 `error: paper_markdown_not_found`**——四级 fallback 全部失败：
   - 看响应的 `pdf_extraction_failed_reason`（PDF 路径试过但失败的提示）：
     - `encrypted` → 「该 PDF 加密；可用 `qpdf --decrypt <in> <out>` 处理后替换 `.paic/library/pdfs/<cite_key>.pdf` 再重 summarize」
     - `empty_extraction` → 「PDF 是扫描版（图像 OCR 才能读）；建议 `ocrmypdf <in> <out>` 加文字层后替换并重试。PAI-C 不自动 OCR」
     - `corrupt` → 「PDF 文件损坏；删 `.paic/library/pdfs/<cite_key>.pdf` 后重 `/paic-ingest <id>`」
   - **没有** `pdf_extraction_failed_reason` 字段（说明项目本地连 PDF 都没有）：
     - **arxiv 论文**：走 arxiv MCP 取文本路径：
       1. `mcp__arxiv__read_paper(paper_id=<id>)` 取 markdown
       2. `mcp__paic__paic_arxiv_pace()` 节流
       3. 重 `mcp__paic__paic_summarize_run(..., paper_text=<那段 markdown>)` —— 走 `caller_supplied` 路径
       4. 仍失败 → `mcp__arxiv__download_paper(paper_id=<id>)` + pace + retry
     - **非 arxiv 论文**（PubMed / bioRxiv / openalex 等）：让用户重 `/paic-ingest <id>` —— 大概率上次 ingest 时下载失败 / 平台不可下载（s2 / google_scholar / ssrn 等）。如果重新 ingest 仍失败，告诉用户「该平台不托管 PDF / 反爬严，PAI-C 暂无法本地化」。

   **`paic doctor` 提示**：用户 fallback 频繁时，告诉他跑 `paic doctor`——能看出 arxiv 存储路径 / pdf 缓存命中等状态。

   **节流要求（fallback 路径必须遵守）**：连续多篇都走 arxiv MCP fallback 时，**每次**
   `mcp__arxiv__read_paper` 或 `mcp__arxiv__download_paper` 之后、进下一篇前，
   先调一次 `mcp__paic__paic_arxiv_pace()`（默认 sleep 6s）。fallback 已经在网络
   上对 arxiv.org 多发了一次请求，并发 fallback 是 429 的最快路径。

   **如果撞了 429 / Too Many Requests**：立刻调 `mcp__paic__paic_arxiv_pace(seconds=20)`
   长 sleep 一次再重试当前篇；连续两次仍 429 → 提示用户「连续 429，要不要 skip 该篇 / 调高
   pace 后从这里继续？」不要硬撞。
   - If `error: paper_not_in_library`, tell the user (中文) to ingest first.
   - If `from_cache: true`, mention briefly. **该篇跳过 pace** —— 没有 arxiv 网络调用，
     无需等待。
   - **If the result contains `mode: "host_orchestration"`**: PAI-C is configured to bypass
     internal LLM calls (订阅模式 / `routing.overrides.summarize: host`). PAI-C has already
     loaded the markdown for you in the response. Do this:
     1. Read the response's `markdown` field — that's the full paper body.
     2. Generate a JSON object that matches the response's `schema_hint`:
        - `problem`: 1–2 paragraphs in English describing the research problem the paper tackles.
        - `method`: 1–3 paragraphs in English describing the proposed approach.
        - `key_results`: list of 3–6 short English bullets (each a single sentence).
        - `limitations`: list of 2–4 short English bullets.
        - `techniques`: list of canonical English technical terms (e.g. "diffusion model",
          "RLHF", "Vision Transformer"). Don't translate.
        - `relevance_to_project` (optional): one English sentence on how this relates to
          the user's project topic, if obvious from the paper-ref's title/abstract.
     3. Call `mcp__paic__paic_summarize_persist(project_dir=<cwd>, paper_id=<id>,
        structured={...your json...})`. **Do NOT** call `paic_summarize_run` again for this
        paper — that re-emits the same orchestration directive.
     4. If persist returns `error: schema_validation_failed`, fix the JSON per its `detail`
        field and retry persist once. If it fails again, tell the user.

     Host orchestration burns no `claude_agent_sdk` / API quota — it uses your current
     conversation's subscription tokens. Don't try to "save tokens" by truncating
     `markdown`; the orchestration directive expects a faithful summary.

3. Render in Chinese:
   - 标题与作者（首两位）
   - 五段：问题 / 方法 / 关键结果 / 局限 / 技术标签
   - 结尾给一行 "完整版: <summary_path>"

## Style
- 中文段落叙述，不用项目符号轰炸（key results 和 limitations 列表保留为短列）
- 论文里的英文术语原样保留（"diffusion model"、"RLHF"、"ImageNet" 等不要翻译）
- 并发策略：
  - **本地命中**（绝大多数情况，markdown 已在 arxiv MCP 本地存储）：多篇可并行调
    `paic_summarize_run`（单条消息多 tool call），它只读本地文件不打 arxiv 网络。
  - **fallback 路径**（任一篇返回 `paper_markdown_not_found`）：**串行**处理这些篇，
    `read_paper` / `download_paper` 之间夹 `paic_arxiv_pace()`，每次只发一篇到 arxiv.org。
  - **host orchestration 模式**（任一篇返回 `mode: "host_orchestration"`）：**逐篇串行**
    处理。每篇要把 markdown 全文进 context 阅读再产出 JSON——并发处理多篇会同时把多份
    markdown 灌进 context，容易爆。一次读一篇、写一次 persist、再下一篇。
