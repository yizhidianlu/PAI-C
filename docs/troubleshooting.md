# Troubleshooting

> **Reference** · `paic doctor` 输出逐行解读、运行时错误目录、`/paic-finalize` 失败排查。

第一道防线：`uv run paic doctor`。它一次跑完所有启动期检查，出错带 `fix:` 提示。**先把 doctor 跑绿再调试运行时**。

---

## `paic doctor` 输出逐行解读

每行格式：`[OK ]` / `[WARN]` / `[ERR ]` / `[--- ]`（skip） + 检查名 + 状态消息。

### `ANTHROPIC_API_KEY`

| 状态 | 含义 | 修复 |
|---|---|---|
| `[OK ] set` | 环境变量已读到 | — |
| `[ERR ] not set, but providers.anthropic.mode is api_key` | 配了 api_key 模式但没设 env | `[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")` 或改 mode 到 `claude_agent_sdk` |
| `[WARN] not set (only needed for api_key mode)` | 没设 env，但走的是 `claude_agent_sdk`，不影响 | 想做 fallback 才需要补 |

### `OPENAI_API_KEY`

| 状态 | 含义 | 修复 |
|---|---|---|
| `[OK ] set` | env 就绪 | — |
| `[--- ] openai provider not configured` | yaml 里没 `providers.openai` 块 | — |
| `[ERR ] not set, but providers.openai is configured` | 配了 openai 但没 env | 补 env，或删 yaml 里的 `providers.openai` 块 |

### `<module> importable`

`anthropic` / `claude_agent_sdk` / `openai` 三个 SDK 检查。`[ERR]` 一般是 `uv sync` 没跑：

```bash
uv sync
```

### `claude CLI on PATH`

| 状态 | 含义 | 修复 |
|---|---|---|
| `[OK ] <path>` | 找到 `claude` 二进制 | — |
| `[ERR ] not found, but anthropic.claude_agent_sdk is referenced` | 配了订阅模式但没装 Claude Code CLI | 装 [Claude Code](https://claude.com/claude-code)，跑 `claude login` |
| `[WARN] not found (only needed for claude_agent_sdk mode)` | 没配订阅模式，警告可忽略 | — |

### `arxiv storage`

按候选根逐行输出：

| 状态 | 含义 | 修复 |
|---|---|---|
| `[OK ] <path>  (N markdown files)` | 找到了 N 个 markdown | — |
| `[WARN] <path>  (0 markdown files)` | 路径存在但空 | 跑一次 `/paic-search` + `/paic-ingest` 触发下载 |
| `[--- ] <path>  (not present)` | 路径不存在，跳过 | — |
| `[WARN] no markdown files found in any candidate root` | 所有候选都空 / 不存在 | 同上，或在 `~/.paic/config.yaml` 加真实路径到 `arxiv_mcp_storage_paths` |

详见 [configuration.md → arxiv 存储路径](configuration.md#arxiv-存储路径)。

### `arxiv pacing`

总是 `[OK]`，消息形如 `batch=1 | delay=6.0s | upstream_throttling=unenforced`：

- `batch=N` — Skill 提示 Claude 一次最多并发的 arxiv 调用数（默认 1）
- `delay=Xs` — `paic_arxiv_pace()` 默认 sleep 时长（默认 6.0s）
- `upstream_throttling=unenforced` — 提示上游 arxiv MCP 自身**不**做下载/读取节流，本地 pacing 是唯一防线

> 默认值是 **6s**：`mcp__arxiv__download_paper` 单次调用内部发 ~2 次 HTTP（HTML probe + PDF），3s pace 实测 4 篇就撞 429。若的 yaml 里显式写了 `inter_batch_delay_sec: 3.0` 又撞了 429，删掉这行回到默认。

值与你期待的不一致？检查 `~/.paic/config.yaml` 的 `providers.arxiv` 块；详见 [configuration.md → arxiv MCP 节流](configuration.md#arxiv-mcp-节流)。

### `semantic scholar`

总是 `[OK]`（S2 是可选服务），消息形如 `rate=0.95 req/s | key=set`：

- `rate=` — 当前生效的客户端节流速率
- `key=set` / `key=anonymous` — env 是否就绪
- `base_url=...` — 只在自定义代理时显示

匿名档 0.33 req/s 仍能跑搜索，只是慢。想升认证档：`[Environment]::SetEnvironmentVariable("SEMANTIC_SCHOLAR_API_KEY", "s2k-...", "User")`。

### `routing`

| 状态 | 含义 | 修复 |
|---|---|---|
| `[OK ] default=anthropic, fallback=..., overrides=N` | 路由配置可解析 | — |
| `[OK ] default=anthropic, overrides=1, host_nodes=summarize` | 路由 OK，且至少 1 个节点走 host orchestration | — |
| `[ERR ] <message>` | yaml 里 routing 块写错（指向未配置的 provider、未知 backend 名等） | 按消息改 `~/.paic/config.yaml` 的 `routing` 块；详见 [configuration.md → 路由](configuration.md#路由-routing) |

`host_nodes` 列表里出现节点名 = 该节点走 host orchestration（PAI-C 不发 LLM 调用，主对话处理）。详见 [Host Orchestration 排错](#host-orchestration-排错)。

---

## 运行时错误

### `mcp 列表里没有 paic`

在 Claude Code 里输 `/mcp` 看不到 paic 条目：

1. `cat ~/.claude.json` 确认 `mcpServers.paic` 这条存在；不在就重跑 `uv run python scripts/register_mcp.py`
2. 跑 `uv run paic serve` 看 MCP server 自身能否启动；如果 import error 说明 `uv sync` 没成功
3. **完全退出** Claude Code 再开（不是 reload）

### `slash command 调用了 /paic:xxx 但找不到`

slash command 使用连字符：`/paic-search`、`/paic-init`（不是 `/paic:search`）。

### `paper_markdown_not_found`

PAI-C 走完四级 fallback 都没找到全文。响应里的 `text_source` 没出现，说明每条都失败：

**四级 fallback 是什么**（自动尝试）：
1. `paper_text=` 旁路（你显式传的）
2. 上游 arxiv markdown（`arxiv_mcp_storage_paths` 里的 `<id>.md`，仅 arxiv 论文）
3. `<project>/.paic/library/pdfs/<pdf_local_path>`（ingest 时写入 `selected.yaml` 的字段；新库为 `NNN_title.md`，老库为 `<cite_key>.md`）
4. 同位置 `.pdf` + pypdf 提取

> **找不到 PDF 文件名**：先看 `selected.yaml` 里这一篇的 `pdf_local_path` 字段——那就是 PAI-C 实际去找的文件名。下面命令里的 `<filename>` 都用这个值（如 `001_attention_is_all_you_need.pdf`）；空字段或老库时退回到 `<cite_key>.pdf`（如 `arxiv_2401_12345.pdf`）。

**修复路径**：

- **响应有 `pdf_extraction_failed_reason`**——PDF 在但提取失败：
  - `encrypted`：用 `qpdf --decrypt <in> <out>`（macOS/Linux 都有 brew/apt 包）解密后替换 `.paic/library/pdfs/<filename>`，重 summarize
  - `empty_extraction`：扫描版 PDF。`ocrmypdf <in> <out>` 跑 OCR 后替换重试。PAI-C 不自动 OCR
  - `corrupt`：删 `.paic/library/pdfs/<filename>` + `/paic-ingest <id>` 重下载
- **响应没有 `pdf_extraction_failed_reason`**（连 PDF 都没有）：
  1. `paic doctor` 看 `arxiv storage` 行，确认根路径 OK
  2. arxiv MCP 自己有没下载完？让 Claude 调 `mcp__arxiv__list_papers` 看
  3. 路径对但 PAI-C 找不到？把 arxiv MCP 的实际存储路径加到 `~/.paic/config.yaml` 的 `arxiv_mcp_storage_paths`
  4. 实在找不到，走 `paper_text=` 旁路（让 Claude 调 `mcp__arxiv__read_paper` 拿文本回喂给 summarize），详见 [configuration.md](configuration.md#arxiv-退路-paper_text-旁路)
  5. **非 arxiv 论文** 没 PDF：重 `/paic-ingest <id>` 触发多平台下载试一次。如果该平台不托管 PDF（s2 / google_scholar / ssrn），PAI-C 暂无法本地化

**pypdf 缓存**：成功提取过的 PDF text 缓存在 `~/.paic/cache/pdf_text/<sha[:16]>.txt`，按文件内容 hash。换了 PDF 自动 miss；强制重提就 `rm ~/.paic/cache/pdf_text/*` 即可。

### `llm_unavailable`

LLM backend 调用失败。从 v0.1.x 起 PAI-C 在错误信息里直接附带 remediation 步骤，**先看 MCP 工具返回的多行错误**——通常已经告诉你下一步。

子状态简表：

| 子状态 | 含义 | 修复 |
|---|---|---|
| `authentication_failed` (claude_agent_sdk) | Claude Code OAuth token 失效 / 过期 | 见下方专门章节 |
| `401 / 403` (api_key) | API key 错或额度耗尽 | 检查 env / 账户余额 |
| `connection refused` (openai.compatible) | 本地 Ollama / vLLM 没起 | 起服务，或把 `base_url` 改对 |

如果 yaml 里配了 `routing.fallback`，default 失败时会自动切到 fallback，graph 不中断。

### `claude-agent-sdk reported authentication_failed`

最常见的运行时错误。PAI-C MCP 是 Claude Code 的子进程，跑 `claude_agent_sdk` 时会 spawn 本机 `claude` CLI 子子进程；CLI 拿不到有效 OAuth token 就抛这个。

**第一步：跑 sdk-probe 拿原始错误**

```bash
uv run paic sdk-probe
```

它会打一次最小往返，把 SDK / CLI 的真实错误原文输出。比从 MCP 工具结果里看更直接。

**按概率排查**：

1. **OAuth token 过期（60%）** — 普通终端跑 `claude login`；通过后用 `claude --print 'hi'` 验证 CLI 能跑通；**完全退出**再开 Claude Code（不是 reload，是 quit + reopen）。
2. **多账号不一致（25%）** — Claude Code 应用登录账号 A，`claude` CLI 登录账号 B 或未登录。`claude_agent_sdk` 用 CLI 那份。CLI 里 `claude logout && claude login` 强制对齐。
3. **MCP 子进程 env 残缺（10%）** — 罕见。`paic sdk-probe` 直接在普通 shell 里跑就**绕过** MCP 子进程；如果它能跑通但 MCP 路径仍挂，就是 env 透传问题；检查 `~/.claude.json` 的 paic 条目 `env` 是否被错改成"覆盖式"。
4. **CLI 与 SDK 版本不匹配（5%）** — `claude --version` + 比对 `claude_agent_sdk` 在 PyPI 的 README；必要时升级 CLI。

**预防性自检**：把上面的 sdk-probe 接入 doctor，可以在还没出错前就发现问题：

```bash
uv run paic doctor --probe
```

这会在常规检查之外多跑一次实活握手（1-3 秒，约 10 token）。

**临时跨过**（如果以上都试过仍要交付）：编辑 `~/.paic/config.yaml`：

```yaml
providers:
  anthropic:
    mode: api_key   # 切到 API key 模式
```

并设 `ANTHROPIC_API_KEY`（要付 API 费用，不再走订阅）。

### S2 429 too many requests

理论上 `_RateLimiter` 已经按 0.95 req/s 客户端节流，不应该撞到。如果仍然 429：

1. 是不是同时跑了多个 PAI-C MCP 进程？rate limiter 是进程内单例，跨进程不共享
2. 系统时钟跳变？`time.monotonic()` 应不受影响，但 NTP 抖动期可能短暂误判
3. 客户端会自动 sleep 2 秒重试一次；持续撞墙就把 `providers.semantic_scholar.rate_limit_per_sec` 调到 `0.5`

### arxiv MCP 429 / 软封 IP

症状：批量 `/paic-ingest`（甚至 4-5 篇就可能撞）或 `/paic-summarize all` 触发 fallback 时，`mcp__arxiv__download_paper` / `read_paper` 报 429 / `Too Many Requests` / `connection reset by peer`，或后续请求挂 60 秒。

**架构限制**：PAI-C MCP 不在 arxiv 调用路径上（Skill 让 Claude 直调 `mcp__arxiv__*`），无法 server-side 拦截。节流由 Skill 通过 `mcp__paic__paic_arxiv_pace` 自愿协作。详见 [configuration.md → arxiv MCP 节流](configuration.md#arxiv-mcp-节流)。

**根因**：`mcp__arxiv__download_paper` 单次调用内部发**多次 HTTP 请求**——先 probe HTML 元数据再退回 PDF。3s pace 实际命中频率 ~0.67 req/s，是 arxiv 限速（1 req/3s = 0.33）的 2 倍。当前默认是 **6s**。

**修复顺序**：

1. **确认默认配置生效** — `paic doctor` 应显示 `arxiv pacing  batch=1 | delay=6.0s | upstream_throttling=unenforced`。如果 `delay=3.0s` 还是 v1 的旧默认 / yaml 显式覆盖：删 `~/.paic/config.yaml` 里的 `providers.arxiv.inter_batch_delay_sec: 3.0` 那行回到 v2 默认；或显式改成 `6.0` / `9.0`。
2. **完全重启 Claude Code**（quit + reopen，不是 reload）让 SKILL.md 改动生效——Claude Code 启动时一次性读 SKILL.md，运行中不会重读。
3. **撞软封了？** arxiv.org 软封大概 60 秒。等 1 分钟再重试。若急着继续可手动加大 sleep：在 Claude Code 里让 Claude 调 `mcp__paic__paic_arxiv_pace(seconds=20)` 等 20 秒（最大 30s 一次）。
4. **当前 ingest 正在跑、撞了 429**：SKILL 应该自动 retry——遇到 429 立即 `paic_arxiv_pace(seconds=20)` 一次再重试；连续 2 次 429 → SKILL 让你决定 skip 该篇还是手工延长 pace。如果 Claude 没这么做，提醒它「请按 SKILL.md 的 429 retry 流程处理」。
5. **持续撞墙** — 通常是 SKILL.md 没装新版（v2 含 429 retry 流程）。重跑 `uv run python scripts/install_skills.py` 把最新 skills 复制到 `~/.claude/skills/`。
6. **Claude 跳过 pace** — 模型有时会"自作主张"加速。把 `inter_batch_delay_sec` 调到 9-10 给 Claude 更明显的吓阻。
7. **6s 也不够（罕见，共享 IP / 大学网）**：试 `inter_batch_delay_sec: 9.0` 或 `12.0`。

### Host orchestration 排错

启用了 `routing.overrides.<node>: host`（详见 [configuration.md → Host Orchestration](configuration.md#host-orchestration) 与 [configuration-cookbook.md § Host orchestration 配置](configuration-cookbook.md#host-orchestration-配置)）后的常见问题：

**`paic doctor` 没看到 `host orchestration` 一行**

仅在存在节点路由到 `host` 时显示。检查：
1. 执行 `cat ~/.paic/config.yaml`，确认包含 `routing.overrides.summarize: host`（位于 `overrides` 而非 `default`）。
2. 完全重启 Claude Code（MCP server 重新加载 config）。
3. 执行 `uv run paic info`，确认 `routing overrides:` 段包含 `summarize → host`。

**SKILL 没接住 host orchestration 模式响应**

老版 SKILL.md 不识别 host orchestration 响应。修复：

```bash
uv run python scripts/install_skills.py
```

把最新 SKILL.md 复制到 `~/.claude/skills/`，然后完全重启 Claude Code。

**Host 模式下持久化反复 schema_validation_failed**

主对话生成的 JSON 不符 schema。响应 `detail` 字段会列出缺 / 错的字段。常见漏：
- `key_results` / `limitations` / `techniques` 没传或不是 list of str
- `problem` / `method` 是空串
- 多了无关字段（PAI-C 严格按 schema 校验）

让 Claude 按 `schema_hint` 重新生成。连续两次失败 → 临时切回非 host 模式（API 模式的 prompt 约束更强）。

**节点报 `HostOrchestrationRequired`**

`paic doctor` 在启动期会校验 `routing.overrides` 中所有 `host` 目标——只有 21 个 host-aware 节点合法（见 [configuration.md § 节点路由表](configuration.md#节点路由表)「host-aware」列）。4 个 cloud-only 节点 `claim_judge` / `paragraph_outline` / `paragraph_write` / `section_coherence_polish` 路由到 `host` 会在 graph 跑到时抛 `HostOrchestrationRequired`。
修复：把这些节点改回云端 backend（`anthropic` / `openai` / 命名 profile）；或不要 `routing.default: host` —— 用 `overrides` 显式覆盖具体节点。

**主对话 context 撑不住 17 篇全文**

host 模式每篇都把 markdown 全文进主对话。论文长（>50KB）或篇数多时主对话 context 会满。SKILL.md 已要求逐篇串行处理，但累计仍有压力。应急：分批跑，比如先 `/paic-summarize 2401.12345`、再下一篇，不用 `all`。

### paper-search-mcp / 多平台检索

启用了 `providers.external_search.enabled: true` 后的常见问题。完整教程见 [`docs/external-search.md`](external-search.md)。

**`paic doctor` 显示 `external search   disabled`**

config 没生效。检查：
1. `cat ~/.paic/config.yaml` 看 `providers.external_search.enabled` 是不是 `true`（不是 `True` / `yes` —— yaml 严格小写 `true`）
2. 完全退出 Claude Code 再开（不是 reload）
3. 重跑 `uv run paic doctor`

**`/mcp` 看不到 `paper_search` 这条**

paper-search-mcp 没注册。

```powershell
# 1) 确认仓库存在（替换 <paper-search-mcp> 为你 clone 的实际路径）
ls <paper-search-mcp>\pyproject.toml

# 2) 装它的依赖
cd <paper-search-mcp>
uv sync

# 3) 注册到 Claude Code（在 PAI-C 仓库根目录运行）
cd <PAI-C_REPO>
uv run python scripts/register_paper_search_mcp.py

# 4) 完全重启 Claude Code
```

如果 paper-search-mcp 不在默认位置（`~/Desktop/paper-search-mcp`），加 `--path <实际路径>`。

**`/paic-search` 没调任何 paper-search-mcp 工具**

Skill 在 `paic_search_strategy` 返回 `enabled=false` 时会走老路径（只 arxiv + S2）。可能原因：
1. `providers.external_search.enabled` 是 false（同上）
2. 项目 `.paic/project.yaml` 没有 `platforms` 字段——重跑 `/paic-init` 选个 domain preset
3. SKILL 是老版——重跑 `uv run python scripts/install_skills.py`

**某平台一直 429**

PAI-C 在 Skill 编排层 voluntary throttling，不是强制。如果某平台特别敏感：

```yaml
providers:
  external_search:
    inter_call_delay_sec:
      pubmed: 1.0          # 默认 0.4，调大到 1 秒
      ssrn: 10.0           # 默认 5，调到 10
```

完全重启 Claude Code。如果仍然 429：
1. 上游 paper-search-mcp 的 retry 会兜底（每平台 3 次重试）
2. 等几分钟让 IP 软封解除
3. SSRN / Google Scholar 这种反爬严的平台天然不稳，建议从 platforms 里去掉

**`ieee` / `acm` / `unpaywall` 总是 skipped**

这三个平台需要 env：

| 平台 | 必需 env |
|---|---|
| ieee | `IEEE_API_KEY` 或 `PAPER_SEARCH_MCP_IEEE_API_KEY` |
| acm | `ACM_API_KEY` 或 `PAPER_SEARCH_MCP_ACM_API_KEY` |
| unpaywall | `UNPAYWALL_EMAIL` 或 `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` |

`register_paper_search_mcp.py` 已经透传，shell 里 `setx VAR value` 之后**完全重启** Claude Code。`paic doctor` 的 `external search` 行不会显示具体哪些缺 key（在 strategy 返回时才检查），但 `/paic-search` 会在 warnings 里报。

**Google Scholar 总是返空 / 报 bot detection**

Google 反爬太严，无 proxy 时容易被封。两条路：

1. 设 `PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL` 指向你的代理 URL
2. 从 platforms 里把 `google_scholar` 去掉，依靠 OpenAlex / Semantic Scholar 的覆盖

### LangGraph run 卡住 / 永远 awaiting_input

```text
/paic-resume
```

列出所有 paused run。如果某个 run 你不想继续：

```text
/paic-resume <run_id>
然后告诉 Claude "cancel this run"
```

底层走 `mcp__paic__paic_runs_cancel`。

### `~/.paic/` 目录没建

`register_mcp.py` 应在首次运行时建。如果没建：

1. 看 `register_mcp.py` 输出有没有报错
2. 手动建：`mkdir -p ~/.paic` + `cp docs/config.yaml.example ~/.paic/config.yaml`
3. 再次 `uv run paic doctor` 验证

### 改了 `~/.paic/config.yaml` 不生效

MCP server 不会热加载 config。**完全退出 Claude Code 再开**。重启之间可跑 `uv run paic info` 验证 yaml 自身解析正确。

---

## `/paic-finalize` 失败排查

`/paic-finalize` 跑 8 类 paper-level 检查（详见 [quality-gate.md](quality-gate.md)）。常见症状：

| 症状 | 处理 |
|---|---|
| `passed=true` 但 issue_count 非零 | 正常，info / minor 不计入 passed 判定 |
| 大量 `unsupported_claims` | 跑 `/paic-draft compose` 让 claims.yaml 留下来；或手改 status / 加 supporting |
| `contribution_consistency` 误报 | abstract / intro / conclusion 用 `\begin{itemize}` 显式列贡献，让计数器准 |
| `numeric_provenance` 把版本号也算 | 那条 claim 改 `type=factual` 或 status=rejected |
| 想跳过某类 | 重跑 `/paic-finalize` 时附 `overrides=["unresolved_todos"]`；持久化要改源数据，详见 [quality-gate.md](quality-gate.md) |

完整 8 类 + override 决策树见 [quality-gate.md](quality-gate.md)。

---

## 想看完整 transcript / 状态

- 评审完整对话：`cat .paic/reviews/<exp_id>/transcript.yaml`
- 当前所有 run：`uv run python -c "from paic.mcp_server.tools.runs import list_runs; print(list_runs())"`，或在 Claude Code 里 `/paic-resume`（不带参数列表）
- 项目全景：`/paic-status`

---

## 还是不行？

- README 里的 `Status` 列表说明哪些 phase 已交付
- 提 issue：https://github.com/yizhidianlu/PAI-C/issues
- 跑 `uv run paic doctor` 把输出贴上去
