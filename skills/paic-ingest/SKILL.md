---
name: paic-ingest
description: Ingest selected papers into the project library — adds them to .paic/library/selected.yaml, downloads PDFs/markdowns via the appropriate per-platform tool (arxiv MCP for arxiv; paper-search-mcp for pubmed / biorxiv / openalex / etc.), and stores a project-local copy at .paic/library/pdfs/<cite_key>.<ext>. Use after /paic-search when the user picks which papers to keep.
allowed-tools: mcp__arxiv__download_paper, mcp__arxiv__search_papers, mcp__paic__paic_library_add, mcp__paic__paic_library_attach_paper, mcp__paic__paic_workspace_status, mcp__paic__paic_arxiv_pace, mcp__paic__paic_search_pace, mcp__paper_search__download_arxiv, mcp__paper_search__download_pubmed, mcp__paper_search__download_biorxiv, mcp__paper_search__download_medrxiv, mcp__paper_search__download_pmc, mcp__paper_search__download_openalex, mcp__paper_search__download_crossref, mcp__paper_search__download_ieee, mcp__paper_search__download_with_fallback
---

# /paic-ingest — add papers to the project library

## When to use
- After `/paic-search` the user says "把 1, 3, 5 加进来" / "ingest these"
- User pastes a list of arXiv ids / DOIs and asks to track them

## What you do

1. If `mcp__paic__paic_workspace_status` shows uninitialized, instruct user to run `/paic-init` and stop.

2. Build a list of paper dicts to add. Each dict should have at least `title` plus one id (`arxiv_id` / `doi` / `s2_id`). If the user references results from a recent `/paic-search`, reuse the dicts from that turn's tool result. If they pasted bare arXiv ids, construct minimal dicts (`{arxiv_id, title}` — fetch the title via `mcp__arxiv__search_papers` if you don't have it; if you need >3 lookups, intersperse `mcp__paic__paic_arxiv_pace()` between calls).

3. **顺序下载（按平台路由）** —— 每篇按其 `platform` / 标识符走对应的下载工具，把文件落到 `<project>/.paic/library/pdfs/<cite_key>.<ext>`。

   **3.0 预检（在循环开始之前做一次）**：
   - 统计待下载论文里**会走 `download_with_fallback`** 的篇数（即下面路由表第 3、4 行 — `openalex` / `crossref` / DOI-only 的论文）。
   - 如果 ≥1 篇要走 fallback，提醒用户一句中文：「本批有 N 篇会走 `download_with_fallback`（Unpaywall + Crossref），需要 `UNPAYWALL_EMAIL` 环境变量；如果没设过，多数 PDF 拿不到。要先去 set 环境变量再重启 Claude Code，还是直接跑（缺的进 'unpaywall_email_missing' 跳过列表）？」
   - 用户回「直接跑」就继续；下载失败的进入未下载列表，summary 注明原因。
   - 如果用户回「set 一下」就停在这里，告诉他们：`[Environment]::SetEnvironmentVariable("UNPAYWALL_EMAIL", "<your@email>", "User")` 然后**完全退出再开 Claude Code**。

   **3.0.5 DOI 前缀 prefilter**（在路由到第 4 / 5 行 fallback 之前先查；**实测能省 ~70% fallback 调用 + 同等量 step 3.3 清理工作量**）：

   按 DOI 前缀分三类。**只对路由到第 4 / 5 行 / 7 行 IEEE fallback / 8 行 ACM fallback 的论文做这步**——arxiv / biorxiv / medrxiv / pmc / 平台原生 download 不受影响。

   | DOI 前缀 | publisher | 路由 |
   |---|---|---|
   | `10.1088/*` | IOP（J. Neural Eng. 等） | 跳过 fallback、直接进 manual list，标 `subscription_journal_no_oa` |
   | `10.1109/*` | IEEE | 同上（IEEE 直连仍按路由表第 7 行先试，失败再到此） |
   | `10.1016/*` | Elsevier（除明确 OA 副本） | 同上 |
   | `10.1002/*` | Wiley | 同上 |
   | `10.1007/*` | Springer（除明确 OA） | 同上 |
   | `10.3389/*` | Frontiers（OA） | 走 fallback |
   | `10.1371/*` | PLOS（OA） | 走 fallback |
   | `10.3390/*` | MDPI（OA） | 走 fallback |
   | `10.1038/s41598-*` | Sci Rep（OA） | 走 fallback |
   | `10.7717/*` | PeerJ（OA） | 走 fallback |
   | 其它前缀 | 灰名单 | 走 fallback + step 3.3 严格三信号校验 |

   **实测污染率（n=11 europepmc fallback，本批 EEG/MI/BCI 主题）**：IOP 2/2 = 100%，IEEE 3/3 = 100%，Elsevier 5/5 = 100%，Frontiers 0/1 = 0%。**根因**：europepmc 在 DOI 没有精确 OA 全文时不报错、silent 返回某个邻近 PMID 的 PDF——订阅期刊几乎必污染，OA 期刊几乎必正确。

   黑名单论文进 manual list 后由 step 4 summary 的「手动下载提示」一并报告，让用户用浏览器手取。

   **3.1 路由表**（按 `paic doctor` 显示的 `batch=N` 处理，默认 1。每篇按下面选**一条**路径）：

   > **`download_with_fallback` 签名**：`(source: str, paper_id: str, doi: str = "", title: str = "", save_path: str = ...)` —— `source` 和 `paper_id` 都是**必填**（位置参数）。下面表里凡是出现 `download_with_fallback(...)` 都遵循这个签名。**纯 DOI 论文**没有平台原生 ID 时，直接把 DOI 当 `paper_id` 传一遍（即 `source="crossref", paper_id=<doi>, doi=<doi>`）—— 这是上游 `crossref_searcher.download_pdf(doi, ...)` 接受的形式，已实测可用。

   | 论文特征 | 下载工具 | Pace 工具 | 落地方式 |
   |---|---|---|---|
   | 有 `arxiv_id`（或 `platform == "arxiv"`） | `mcp__arxiv__download_paper(paper_id=<arxiv_id>)` | `mcp__paic__paic_arxiv_pace()` | 上游写到 `~/.arxiv-mcp/papers/`；之后调 `paic_library_attach_paper(project_dir, paper)` 自动 copy 一份到 `library/pdfs/<cite_key>.md` |
   | `platform == "pubmed"`（**注意**：`download_pubmed` 是 stub，永远返回 "not supported"；上游 NCBI 不允许程序化下 PMID 的 PDF。**不要直调** `download_pubmed`） | `mcp__paper_search__download_with_fallback(source="pubmed", paper_id=<PMID>, doi=<paper.doi 或 "">, save_path="<cwd>/.paic/library/pdfs/")` —— fallback 内部还会试 PMC OA、Unpaywall、Sci-Hub | `mcp__paic__paic_search_pace(platform="pubmed")` | save_path 是目录 + 必须 rename（见 3.2）。fallback 全链路失败标 "未下载（pubmed_no_oa）" |
   | `platform` 是 `biorxiv` / `medrxiv` / `pmc` 之一（这三个 `download_pdf` 是真实下载、不是 stub） | `mcp__paper_search__download_<platform>(paper_id=<doi 或 PMCID>, save_path="<cwd>/.paic/library/pdfs/<cite_key>.pdf")` | `mcp__paic__paic_search_pace(platform="<platform>")` | 这几个上游接受**文件形式** save_path，直接落地，**不**用 attach |
   | `platform == "openalex"`（openalex **不托管 PDF**，但永远带 DOI；不要调 `download_openalex` / `download_with_fallback(source="openalex")`，二者都会报 `Unsupported source`） | `mcp__paper_search__download_with_fallback(source="crossref", paper_id=<paper.doi>, doi=<paper.doi>, save_path="<cwd>/.paic/library/pdfs/")` | `mcp__paic__paic_search_pace(platform="crossref")` | save_path **是目录、不是文件路径**；上游自决文件名（如 `europepmc_PMID_*.pdf` / `unpaywall_<doi>.pdf`）；**下载成功后必须 rename**（见 3.2）|
   | `platform == "crossref"` 或 有 `doi` 但 `platform` 不在以上几行（含未识别 platform 的 fallback） | `mcp__paper_search__download_with_fallback(source="crossref", paper_id=<doi>, doi=<doi>, save_path="<cwd>/.paic/library/pdfs/")` | `mcp__paic__paic_search_pace(platform="crossref")` | 同上：save_path 是目录 + 必须 rename |
   | `platform == "ieee"`（IEEE 论文带 `pdf_url` 但通常**需要机构 IP / 订阅**） | 先试 `mcp__paper_search__download_ieee(paper_id=<paper.paper_id 或 doi>, save_path="<cwd>/.paic/library/pdfs/")`；返回 401/403/`access denied`/`not authorized`/空 PDF → 退到 `mcp__paper_search__download_with_fallback(source="crossref", paper_id=<paper.doi>, doi=<paper.doi>, save_path="<cwd>/.paic/library/pdfs/")` | `mcp__paic__paic_search_pace(platform="ieee")` 主路；fallback 后再 `paic_search_pace(platform="crossref")` | save_path 是目录 + 必须 rename（见 3.2）。两条都失败 → "未下载（ieee_paywalled）"——见**已知陷阱**关于 IOP/IEEE OA 命中率 |
   | `platform == "acm"`（ACM 数字图书馆**没有官方 PDF API**，paper-search-mcp 的 `download_acm` 是 NotImplementedError stub） | **不要调** `download_acm`；直接 `mcp__paper_search__download_with_fallback(source="crossref", paper_id=<paper.doi>, doi=<paper.doi>, save_path="<cwd>/.paic/library/pdfs/")` | `mcp__paic__paic_search_pace(platform="crossref")` | save_path 是目录 + 必须 rename。多数 ACM 论文有 ACM Open OA 副本可拿到；纯付费墙的标 "未下载（acm_no_oa）" |
   | 没 arxiv_id / platform 不可下载（s2 / google_scholar / ssrn / iacr / 缺 paper-search-mcp / 缺 UNPAYWALL_EMAIL / 缺 IEEE_API_KEY） | **跳过下载** | — | 仅 metadata，summary 标 "未下载" 并给原因 |

   > **注意**：进入第 4 / 5 行 fallback 之前必须先过 step 3.0.5 DOI 前缀 prefilter；订阅墙黑名单 DOI 不进 fallback。

   **3.2 `download_with_fallback` 后的 rename 流程**（针对路由表里所有调 `download_with_fallback` 的行 —— pubmed / openalex / crossref / ieee fallback / acm）：
   1. 调 `download_with_fallback` 前先记录 `library/pdfs/` 当前文件清单（或记 `download_with_fallback` 返回的 `file_path` / `path` / `filename` 字段——上游通常会返回实际写盘路径）。
   2. 下载成功后从返回值拿 `file_path`，否则在 `library/pdfs/` 里找新增的文件（与 step 1 的清单 diff）。
   3. 用 shell rename：`Move-Item <实际文件名> <cite_key>.<原扩展名>`（PowerShell）或 `mv ...`（bash）；扩展名沿用上游的（多数是 `.pdf`，偶尔 `.xml`）。
   4. **不要**强行 rename 成 `.pdf` —— 如果上游返回 XML 全文，summarize 走 pypdf fallback 会失败，让 `.xml` 保留即可（summarize 逐级 fallback 时还能撞到 `library/pdfs/<cite_key>.md` 等候补）。
   5. Rename 失败（如目标已存在）→ 当作下载成功处理，summary 注明 "已存在，未覆盖"。

   **3.3 europepmc / fallback 内容校验**（针对 step 3.2 来源的所有文件，**必做**——`download_with_fallback` 内 europepmc 路径会 silent 返回不相关论文的 PDF）：

   1. 适用对象：所有走过 step 3.2 rename 流程的篇（pubmed / openalex / crossref / ieee fallback / acm；尤其上游原始文件名以 `europepmc_PMID_` / `unpaywall_` 起头的）。直接走平台原生 download 的（arxiv / biorxiv / medrxiv / pmc）按 paper_id 拿精确 PDF，**不需要**校验。
   2. 抽 page-1 文本（**前 1500 字符**——500 太短，很多期刊前 500 字符全是 journal header / DOI / copyright / affiliation 邮编，标题/作者要 800-1500 字符才出现）：

      PowerShell / cmd（Windows 任一 locale 通用）：
      ```
      uv run python -c "import pypdf,sys; sys.stdout.reconfigure(encoding='utf-8',errors='replace'); print(pypdf.PdfReader(sys.argv[1]).pages[0].extract_text()[:1500])" <cite_key>.pdf
      ```

      bash / zsh：
      ```
      uv run python -c "import pypdf,sys; print(pypdf.PdfReader(sys.argv[1]).pages[0].extract_text()[:1500])" <cite_key>.pdf
      ```

      或 `pdftotext -l 1 <cite_key>.pdf -` 二选一。

      Windows 命令里的 `sys.stdout.reconfigure(...)` 是必须的——中文 locale 下默认 stdout 走 GBK，遇到 PDF 几乎一定有的 ©/希腊字母/Unicode dash 会抛 `UnicodeEncodeError`。
   3. 多信号校验（**全部满足**才算 OK，任一不满足即标 europepmc_wrong_paper）：

      - **(a) 作者姓氏命中（必要条件、最强信号）**：取 paper.authors[0]（dict 形式取 `family` 字段；str 形式拆 token 取最后一个 ≥2 字符 token）的 surname，在抽出的前 1500 字符里 case-insensitive substring 必须命中。surname 不命中 → 直接判错位，不再看 (b) (c)。
      - **(b) title content word 命中率**：title 拆词去 stopword（the/a/an/of/and/with/for/in/on/by/to/from/via/using/based/towards）+ 去 ≤2 字符 token，distinct content word 在抽出的前 1500 字符里命中比例 ≥ **75%**。
      - **(c) 兜底字符串相似度**：`difflib.SequenceMatcher(None, title.lower(), extracted.lower()).find_longest_match(...)`，最长公共子串字符数 ≥ title 字符数 × **0.4**。

      ⚠️ **高频词重叠领域警告**（EEG/MI/BCI、单细胞测序、diffusion 视频生成、protein folding、climate-AI、quantum chemistry 等）：(b) 单独不可靠——本批 EEG/MI/BCI 实测 4-6/8 词命中的 PDF 全是错论文。**(a) 作者姓氏校验是这类主题下唯一可靠的强信号**，必须命中。
   4. **同 size 文件优先查**：`library/pdfs/` 多个原始 europepmc 文件 size 完全一致（或 rename 后 `<cite_key>.pdf` 大小相同）→ 大概率同一污染源；一个污染则同 size 的全列入嫌疑。
   5. 命中污染（任一信号不满足）：
      1. **列清单给用户**（不要直接删）：cite_key + DOI（或 arxiv_id）+ 实际抽出的标题/作者前 80 字符 + 不命中的信号（surname / title 词率 / 字符相似度）
      2. **问一句中文**：「这 N 篇 europepmc fallback 内容错位（[原因列表]），要删除并标 europepmc_wrong_paper 吗？」
      3. 用户确认后才删，**逐篇** `Remove-Item <cite_key>.pdf`（PowerShell）/ `rm <cite_key>.pdf`（bash）——**不要**一条命令删多个文件。harness 默认拦批量删除 pre-existing PDF（reason 里会写 "Mass deletion of pre-existing local PDF files based on an unverified heuristic check"），这不是绕路而是必须的授权礼仪。
      4. 删完逐篇标 `europepmc_wrong_paper` 进未下载列表
      5. 用户回「不删」/「我要先看一下」 → 保留文件，summary 标 `europepmc_suspect_kept`，让用户在 `/paic-summarize` 跑前再决定是否删
      - **不要**默认让可疑 PDF 进下游——pypdf 抽出来的大段无关文本会污染 `/paic-summarize` 的 chunk embedding 和 `/paic-draft` 的引用
   6. 校验过关：保留文件，summary 不提

   **cite_key 计算**（与 BibTeX 一致）：
   - `arxiv_id`: `arxiv_<arxiv_id 里非字母数字替换为 "_">`（如 `2401.12345` → `arxiv_2401_12345`）
   - `doi`: `doi_<doi 里非字母数字替换为 "_">`（如 `10.1234/abc` → `doi_10_1234_abc`）
   - `s2_id`: `s2_<id>`
   - 否则 `<title 第一个词>_<year>`

   **重要约束**：
   - **串行处理**：单消息内不要并发多个 `download_*`，会撞限流（arxiv 默认 6s pace 已对齐 1 req/3s；paper-search-mcp 各平台 pace 见 `paic_search_strategy` 返回）
   - **遇 429 / Too Many Requests / connection reset** → 走 step 3a 的 retry 流程
   - **paper-search-mcp 未注册**（`mcp__paper_search__*` 不在 allowed-tools 实际可用集里）→ 非 arxiv 论文全部跳过下载，summary 提示用户跑 `register_paper_search_mcp.py`
   - **`Unsupported source` 错误** → 说明该 platform 不在 `download_with_fallback` 的支持列表里（已知不支持：`openalex`、`s2` / `semantic`、`google_scholar`）。openalex 论文应该用 `source="crossref", paper_id=<doi>, doi=<doi>` 走 DOI fallback；`download_openalex` / `download_with_fallback(source="openalex")` 都不要调。
   - **缺 `source` / `paper_id` 报错** → `download_with_fallback` 的签名是 `(source, paper_id, doi="", title="", save_path="...")`。**纯 DOI 论文**没有平台原生 ID 时，把 DOI 当 `paper_id` 传一遍即可：`source="crossref", paper_id=<doi>, doi=<doi>`（实测可用，因为 `crossref_searcher.download_pdf` 接受 DOI 作为 paper_id）。**不要**省 `source` 或 `paper_id`。
   - **缺 UNPAYWALL_EMAIL** → `download_with_fallback` 的多数 publisher 路径走 Unpaywall API；缺 email 时 fallback 链短一截，命中率显著下降。看到 `UNPAYWALL_EMAIL not set` 类错误时把这一篇标 "未下载（unpaywall_email_missing）" 跳过，不要 retry。
   - **IEEE 401/403/`access denied`** → 用户没有机构订阅访问权；不要 retry IEEE 直连，直接退到 `download_with_fallback(doi=...)`。两条都失败 → "未下载（ieee_paywalled）"，summary 注明可以让用户尝试 sci-hub / 校园 VPN。
   - **`download_acm` NotImplementedError** → 不是配置错误；ACM 没有官方 PDF API。SKILL 路由表已经把 ACM 直接指向 `download_with_fallback`，正常情况你不会调到这个工具。如果误调了直接当作 "未下载（acm_no_api）" 处理。
   - **`download_with_fallback` 抛 `'int' object has no attribute 'strip'`** → 上游 paper-search-mcp 解析某些 crossref / europepmc metadata 时把整数字段当字符串 `.strip()`，间歇报错（约 1/4 fallback 命中）。**与 `use_scihub` / `UNPAYWALL_EMAIL` 无关、无解、不要 retry**，标 "未下载（download_with_fallback_int_bug）" 跳过即可。

   全部下载结束后**一次性**调 `mcp__paic__paic_library_add(project_dir=<cwd>, papers=[...], tags=[...])` 注册所有论文（library_add 不打网络，可以一次性传整批）。

   **3a. 429 retry 流程**（任何 platform 撞限流时通用）：
   1. arxiv 用 `mcp__paic__paic_arxiv_pace(seconds=20)`；其它平台用 `mcp__paic__paic_search_pace(platform="<platform>", seconds=20)`
   2. 重试当前篇的 download
   3. 仍 429 → 同样 `paic_*_pace(seconds=30)` 最后一次重试
   4. 第 3 次还 429 → **停下来问用户**："这一篇连续 3 次 429，是 skip 还是把 yaml 的 inter_batch_delay_sec / inter_call_delay_sec.<platform> 调到 10s 后从这篇继续？" 不要再硬撞。
   5. 成功后用一行中文告知"第 N 篇 429 撞过，多 sleep XXs 重试通过"。

4. Render a short Chinese summary:
   - 已纳入 N 篇 (列 title)
   - **本地存档**：`.paic/library/pdfs/` 下新增了 X 个文件（列前几个 cite_key + 扩展名，如 `arxiv_2401_12345.md`、`doi_10_1234_abc.pdf`）
   - **rename 命中**：Y 次（download_with_fallback 自定义文件名 → 改回 `<cite_key>.<ext>`）
   - **未下载**：M 篇（按原因分组：`s2 不托管 PDF` / `paper-search-mcp 未注册` / `unpaywall_email_missing` / `Unsupported source` / `pubmed_no_oa` / `ieee_paywalled` / `acm_no_oa` / `publisher_referer_block`（IOP/IEEE OA 但被发布商拦） / `连续 3 次 429 用户决定 skip` / `download_with_fallback 全链路失败` / `download_with_fallback_int_bug` / `europepmc_wrong_paper` / `europepmc_suspect_kept` / `subscription_journal_no_oa`）
   - **手动下载提示**：若 `europepmc_wrong_paper` / `subscription_journal_no_oa` / `publisher_referer_block` / `ieee_paywalled` 任一 ≥1，列出受影响论文（cite_key + DOI/arxiv_id + 期望落盘路径 `library/pdfs/<cite_key>.pdf`），告诉用户「这些篇 silent corruption / 订阅墙 / 发布商防护，请浏览器手取 PDF 放到上面对应路径，attach 是 idempotent 的，下次 `/paic-summarize` 会自动用上」。
   - 跳过 K 篇 (重复; 列 arxiv_id)
   - 429 retry 命中：J 次（如有）
   - 当前库存: T 篇

## Style
- 中文。
- 不要等下载完成才回复——`mcp__arxiv__download_paper` 是异步语义，登记到 library 即可视为成功。
- 若用户没指定 tags，留空列表。
- **耗时预期**：17 篇 ingest 在默认（batch=1, pace=6s）下约 102s（17 × 6s pace + 网络）。如果用户嫌慢且**网络稳定无 429 历史**，可改 `~/.paic/config.yaml` 的 `providers.arxiv.inter_batch_delay_sec` 到 4 或 5（自担 429 风险），重启 Claude Code 后再跑。**不要**主动跳过 pace 或调到 3 以下，会触发 429。
- **看到 429 不要慌**——按 step 3a 的 retry 流程跑；这是已知风险，不是配置错误。

## 已知陷阱
- arxiv MCP 不同版本默认存储路径不一样（`~/Documents/arxiv-papers/` vs `~/.arxiv-mcp/papers/`）。PAI-C 默认两个都探，但若用户的安装把文件放到第三处，`/paic-summarize` 会回 `paper_markdown_not_found`。处理方式见 `paic-summarize` skill —— **不要**手动复制目录绕行；让 `/paic-summarize` 通过 `mcp__arxiv__read_paper` 取文本再传 `paper_text=`。
- 第一次使用时建议提示用户跑 `uv run paic doctor`，提前发现路径与凭证问题。
- **paper-search-mcp 的 download 工具签名**：约定接受 `paper_id` + `save_path`，但不同 platform 的具体参数名可能略有差异（如 `pmid` vs `paper_id`、`pmcid` 等）。第一次跑某 platform 时如果工具报参数错误，从工具的错误响应里看到正确字段名后调整。`download_with_fallback` 实际签名是 `(source, paper_id, doi="", title="", save_path=...)` —— `source` + `paper_id` 都必填，**不是**只接 `doi`。
- **`download_pubmed` 是 stub**：上游 `paper_search_mcp/academic_platforms/pubmed.py` 的 `download_pdf` 直接返回 "PDF download not supported"——NCBI E-utilities 不允许程序化下 PMID 的 PDF。**不要直调** `download_pubmed`；走 `download_with_fallback(source="pubmed", paper_id=<PMID>, doi=<DOI>)`，fallback 内部会试 PMC OA → Unpaywall → Sci-Hub。
- **同样别直调的 stub**：`download_acm`（ACM 没官方 PDF API，永远 NotImplementedError）。SKILL 路由表已经把 ACM 直接指向 `download_with_fallback`。
- **`download_with_fallback` 的 `save_path` 是目录、不是文件路径**（实测）：传文件路径上游会自决文件名，把文件丢到该路径所在目录里——典型表现是 `library/pdfs/` 下出现 `europepmc_PMID_<n>.pdf`、`unpaywall_<doi>.pdf` 而不是 `<cite_key>.pdf`。SKILL 的 step 3.2 强制 rename 闭合这个差异。
- **`Unsupported source`**：`download_with_fallback(source=...)` 的 source 白名单是上游内部硬编码，目前已知 `openalex` / `s2` / `semantic` / `google_scholar` 不支持。openalex 走 DOI 路径（路由表第 3 行）；其它不支持的 source 直接列入未下载。
- **`paic doctor` 已经会检查 UNPAYWALL_EMAIL / IEEE_API_KEY / ACM_API_KEY**（`credential: <platform>` 行）：当 `external_search.enabled=true` 且对应平台在当前 preset / 用 download_with_fallback 时缺 env，会有 WARN 行 + fix 提示。SKILL 的 step 3.0 预检仍保留作为运行时兜底（用户可能跳过了 doctor）。
- **IEEE / ACM 的下载现状**：IEEE 直连需要机构订阅，多数家庭网络拿不到 PDF（API 返回 401/403）；ACM 没有官方 PDF API，paper-search-mcp 的 `download_acm` 是空实现。两个平台都靠路由表里的 `download_with_fallback(source="crossref", paper_id=<doi>, doi=<doi>, ...)` 走 Unpaywall / Crossref / OA repo 兜底。
- **IOP / IEEE 期刊 OA 链接命中率 <20%**（实测）：即便 Unpaywall / Crossref 找到了 OA URL，IOP（J. Neural Eng. 等）和 IEEE（TNSRE / TII / TBME 等）的 PDF endpoint 普遍有 referer / cookie / Cloudflare 防护，工具会拿到 `resolved OA URL but download failed`。这**不是** `UNPAYWALL_EMAIL` 缺失导致的——已经登记 email 也照样拦。看到这种错误就标 "未下载（publisher_referer_block）" 跳过，不要 retry；让用户拿 DOI 用浏览器手取或走校园 VPN，再 `paic_library_attach_paper` 手工塞进 `library/pdfs/<cite_key>.pdf`。Springer / Wiley / Elsevier 的非 OA 文章也有类似行为，但命中率经验值 30-60%，比 IOP/IEEE 略好。
- **library/pdfs/ 占空间**：100 篇 × ~3MB ≈ 300MB。可以加 `.paic/library/pdfs/*` 进 `.gitignore` 不进 repo；attach 是 idempotent 的，删了重 ingest 即可恢复。
- **paper-search-mcp 没注册**：SKILL allowed-tools 里有这些工具但实际调用会失败（"tool not available"）。这种情况下非 arxiv 论文 silently skip 下载、只 ingest metadata；summary 提示用户「装上 paper-search-mcp 后重 ingest 就有 PDF 了」（教程在 docs/external-search.md）。
- **`download_with_fallback` 'int' object has no attribute 'strip'**：上游 paper-search-mcp 解析某些 crossref / europepmc metadata 时把整数字段当字符串 `.strip()`，间歇报错——与 `use_scihub` / `UNPAYWALL_EMAIL` 都无关。本批实测约 **1/4** fallback 命中。**无解、上游问题**；遇到直接标 `download_with_fallback_int_bug` 跳过，不要 retry，也不要试图换参数。
- **🚨 europepmc fallback silent corruption（严重）**：上游按 DOI 找不到精确 OA 全文时，europepmc **不报错**、却返回**完全不相关论文**的 PDF（按 PMID 文件名落地，如 `europepmc_PMID_38xxxxxx.pdf`）。**污染率高度依赖 publisher**——两次实测合计 24 篇 fallback：

  | publisher | 污染率 | 备注 |
  |---|---|---|
  | IOP / IEEE / Elsevier / Wiley / Springer 订阅期刊 | ≈ 100% | 几乎必污染 |
  | Frontiers / PLOS / MDPI / Sci Rep / PMC OA | ≈ 0% | 真 OA，正常下载 |

  比 'int' bug 危险得多——silent corruption 会污染后续 `/paic-summarize` 的 chunk embedding 和 `/paic-draft` 的引用。

  - **预防**：step 3.0.5 的 DOI 前缀 prefilter 把订阅墙黑名单（10.1088 / 10.1109 / 10.1016 / 10.1002 / 10.1007）提前剔除、不进 fallback；OA 白名单（10.3389 / 10.1371 / 10.3390 / 10.1038/s41598- / 10.7717）正常走 fallback；其它灰名单走 fallback + step 3.3 严格校验。
  - **检测**：rename 完之后立刻抽 page-1 前 1500 字符，按 step 3.3 三信号校验：(a) 作者姓氏命中（必要、强信号；高频词领域唯一可靠的判据）+ (b) title content word 命中率 ≥ 75% + (c) 字符串相似度兜底。
  - **行动**：任一信号不满足 → **列清单给用户问授权后**才删 PDF（harness 默认拦批量删除 pre-existing 文件，必须先授权）+ 标 `europepmc_wrong_paper` + 进未下载列表。
  - **用户侧**：summary 单独列出 `europepmc_wrong_paper` 和 `subscription_journal_no_oa` 两类，告诉用户用浏览器拿 DOI 手取 PDF 放到 `library/pdfs/<cite_key>.pdf`，下次 summarize 会自动用上（attach idempotent）。
  - SKILL step 3.0.5 + 3.3 已把这套自动化。
