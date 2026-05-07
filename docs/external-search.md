# 多平台论文检索

PAI-C 默认在 `/paic-search` 里调两条源：arXiv（经现有 arxiv MCP）+ Semantic Scholar（经 `paic_s2_search`）。这对 CS/ML 用户够用，但生物医学 / 经济 / 工程等领域离不开 PubMed、bioRxiv、IEEE Xplore、SSRN 这些专属库。

PAI-C 通过 opt-in 接入上游 [`paper-search-mcp`](https://github.com/openags/paper-search-mcp)（独立 MCP server，覆盖 20+ 平台），让你在 `/paic-init` 时按研究领域选一组「论文来源平台」，`/paic-search` 自动 fan-out 到这些平台 + 现有两源 + 跨平台去重。

> **PAI-C 不替换 arxiv MCP**——arxiv 仍走 `mcp__arxiv__*`（保留现有 ingest / summarize 流程）；paper-search-mcp 只是作为补充扩展接进来。

## 适用场景

| 研究领域 | 推荐平台 | preset |
|---|---|---|
| 计算机 / AI / NLP / CV / 统计 | arxiv, semantic_scholar, openalex, dblp | `cs_ml` |
| 生物医学 / 临床 / 生命科学 | pubmed, biorxiv, medrxiv, europepmc, pmc | `biomed` |
| 物理 / 数学 / 天文 | arxiv, semantic_scholar, openalex | `physics_math` |
| 经济 / 金融 / 管理 / 社会学 | ssrn, openalex, semantic_scholar, crossref | `econ_social` |
| 工程 / 电子 / 机械 / 材料 | arxiv, openalex, crossref, ieee | `engineering` |
| 跨学科 / 综合 / 默认 | arxiv, semantic_scholar, openalex, crossref, doaj | `interdisciplinary` |

`arxiv` 与 `semantic_scholar` 在所有 preset 里都自动加上（不重复）。其余平台都通过 paper-search-mcp 调用。

## 安装与配置

### 第 1 步：装 paper-search-mcp

```powershell
cd <paper-search-mcp>     # 你 clone paper-search-mcp 的实际路径
uv sync
```

### 第 2 步：注册到 Claude Code

```powershell
cd <PAI-C_REPO>            # PAI-C 仓库根目录
uv run python scripts/register_paper_search_mcp.py
```

`scripts/register_paper_search_mcp.py` 默认在 `~/Desktop/paper-search-mcp` 找上游。若装在别处，用 `--path` 指定：

```powershell
uv run python scripts/register_paper_search_mcp.py --path <paper-search-mcp 实际路径>
```

或设环境变量 `PAPER_SEARCH_MCP_HOME`。

### 第 3 步：开启 PAI-C 的多平台路径

编辑 `~/.paic/config.yaml`，找到 `providers.external_search` 段（若装 PAI-C 时没拉新版 example，手动加上）：

```yaml
providers:
  external_search:
    enabled: true
    default_preset: interdisciplinary
```

把 `enabled` 改成 `true`。**完全退出 Claude Code 再重开**——MCP server 注册不热加载。

### 第 4 步：验证

```powershell
uv run paic doctor
```

应有：

```text
[OK ] external search   enabled=true | preset=interdisciplinary | max_results=10 | upstream_throttling=per-platform
```

`/mcp` 在 Claude Code 会话里应同时看到 `paic` 和 `paper_search` 两条。

## 在新项目里使用

`/paic-init` 在 multi-platform 模式下会主动询问研究领域：

```text
你: /paic-init
Claude: 这是个新论文项目。研究领域是哪个？
        1. cs_ml — 计算机/AI/ML
        2. biomed — 生物医学
        3. physics_math — 物理/数学
        4. econ_social — 经济/社会学
        5. engineering — 工程
        6. interdisciplinary — 跨学科（默认）
        7. 自定义
你: biomed
Claude: 好，biomed 默认开启 [pubmed, biorxiv, medrxiv, europepmc, pmc, semantic_scholar, arxiv]
        要加减吗？(回车保持原样)
你: 去掉 medrxiv
Claude: 写入 .paic/project.yaml...
```

`.paic/project.yaml` 会包含：

```yaml
project_id: 01HXXXXX...
title: ...
domain_preset: biomed
platforms:
  - arxiv
  - semantic_scholar
  - pubmed
  - biorxiv
  - europepmc
  - pmc
```

## 在已有项目里加平台

直接编辑 `.paic/project.yaml` 加 / 改 `platforms` 字段即可：

```yaml
platforms:
  - arxiv
  - semantic_scholar
  - openalex
  - crossref
```

或重跑 `/paic-init` 显式给：

```text
你: /paic-init platforms_override=[pubmed, biorxiv, openalex]
```

## 限流（rate limiting）

每个平台都有自己的速率限制。PAI-C 在 `paic_search_pace(platform=...)` 工具里给每个平台设了**保守的默认延迟**（见 `~/.paic/config.yaml` 的 `providers.external_search.inter_call_delay_sec`），Skill 在 fan-out 期间每发一个平台请求 → 立即调一次 `paic_search_pace` → 再下一个平台。

| 平台 | 默认延迟 | 上游限制 | 备注 |
|---|---|---|---|
| pubmed | 0.4s | NCBI: 3 req/s 无 key, 10 req/s 有 key | 严守，避免 NCBI 软封 |
| biorxiv / medrxiv | 1.0s | 不公开 | 保守 |
| europepmc / pmc | 0.4s | EBI 友好 | |
| crossref | 0.05s | Polite pool ~50 req/s | 设 UNPAYWALL_EMAIL 进 polite pool |
| openalex | 0.1s | Polite pool | |
| core | 1.5s | 10 req/min 无 key | 强烈建议设 CORE_API_KEY |
| ssrn | 5.0s | Cloudflare 反爬严 | 慢点更稳 |
| google_scholar | 5.0s | Bot detection | 建议设 PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL |
| ieee / acm | 1.0s | 因合同而定 | 缺 key 自动跳过 |

### 调优

如果某平台经常 429：

```yaml
providers:
  external_search:
    inter_call_delay_sec:
      pubmed: 1.0       # 改成 1 秒，比默认 0.4 秒更慢
```

如果某平台一直没问题想加速：

```yaml
providers:
  external_search:
    inter_call_delay_sec:
      crossref: 0.02    # Crossref polite pool 容得下
```

完全重启 Claude Code 让改动生效。

## 可选 API key

paper-search-mcp 的部分平台需要外部 key / email。这些环境变量都是**可选**——缺失时 PAI-C 自动跳过对应平台并 warning：

| 环境变量 | 用途 |
|---|---|
| `IEEE_API_KEY` 或 `PAPER_SEARCH_MCP_IEEE_API_KEY` | IEEE Xplore（缺则跳过 ieee 平台） |
| `ACM_API_KEY` 或 `PAPER_SEARCH_MCP_ACM_API_KEY` | ACM Digital Library（缺则跳过 acm） |
| `UNPAYWALL_EMAIL` 或 `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` | Unpaywall 要求 email contact（缺则跳过 unpaywall） |
| `CORE_API_KEY` 或 `PAPER_SEARCH_MCP_CORE_API_KEY` | CORE：免费但强烈推荐（无 key 限速 10 req/min） |
| `SEMANTIC_SCHOLAR_API_KEY` | S2：提速到 1 req/s（无 key 是 ~0.33 req/s） |
| `GOOGLE_SCHOLAR_PROXY_URL` 或 `PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL` | Google Scholar：建议配代理 URL |

`register_paper_search_mcp.py` 已经把这些都从父进程透传给 paper-search-mcp 子进程——你只需要在 shell 里 `setx` / `export` 即可。

## 工作原理

PAI-C 的 Python 进程**不在** paper-search-mcp 的调用路径上：Claude 在 Skill 指引下直调 `mcp__paper_search__search_<platform>`，绕过 PAI-C Python（与 arxiv 节流同处境）。所以节流是 voluntary：

1. Skill 调 `paic_search_strategy(project_dir=...)` 拿到「该项目要查哪些平台 + 各自 pacing」
2. 对每个 platform：
   - 调 `mcp__paper_search__search_<platform>(query=..., max_results=...)`
   - 立即调 `paic_search_pace(platform="<platform>")` 阻塞 sleep
3. 收齐所有平台结果后调 `paic_dedupe(papers=...)` 跨平台合并（DOI / arxiv_id / 标题模糊）
4. 最终渲染中文表格给用户

如果 Claude 漏调 `paic_search_pace`，节流就失效——只能靠 paper-search-mcp 自身的 retry 兜底。Skill prompt 里写得很死，但毕竟是协作式约束。

## 常见问题

参见 [`troubleshooting.md`](troubleshooting.md) 的 `paper-search-mcp` 段。
