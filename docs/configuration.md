# Configuration 参考：`~/.paic/config.yaml` 全字段

`register_mcp.py` 首次运行时会从 [`docs/config.yaml.example`](config.yaml.example) seed 一份到 `~/.paic/config.yaml`。**已存在则不覆盖**，所以你之后改的不会被脚本擦掉。

> 改完 `~/.paic/config.yaml` 必须**完全重启 Claude Code**，MCP server 不会热加载。

随时可跑 `uv run paic info` 看当前生效的解析结果，或 `uv run paic doctor` 验证整体健康。

---

## 顶层字段

```yaml
default_model: claude-opus-4-7         # legacy 字段；providers.anthropic.model 优先
arxiv_mcp_storage_paths: [...]         # 见下文「arxiv 存储路径」
providers: {...}                       # 见下文「Provider」
routing: {...}                         # 见下文「路由」
```

> 所有 `providers` / `routing` 字段都可省，省略时回退到 v0.1 的「单一 anthropic.api_key + ANTHROPIC_API_KEY」行为。

---

## arxiv 存储路径

PAI-C 自身不下载 arXiv 论文，由 [arxiv MCP](https://github.com/blazickjp/arxiv-mcp-server) 写到本机；PAI-C 直接读那些 markdown 文件做摘要。

**默认探测顺序**（按出现顺序找第一个含文件的）：

1. `~/Documents/arxiv-papers/`
2. `~/.arxiv-mcp/papers/`

—

**自定义路径**（用户配置加在最前，内置默认仍作为兜底追加）：

```yaml
# 多值（推荐新格式）
arxiv_mcp_storage_paths:
  - ~/Documents/arxiv-papers/
  - /mnt/data/arxiv/
```

```yaml
# 单值（旧格式，仍兼容）
arxiv_mcp_storage_path: ~/Documents/arxiv-papers/
```

`uv run paic doctor` 会逐根列出 `[OK] arxiv storage   <path>  (N markdown files)`，没文件就 `[WARN]`，缺路径 `[---]`。

### arxiv 退路 paper_text 旁路

PAI-C 在所有候选根都找不到 markdown 时，`paic_summarize_run` 工具支持 `paper_text=<full markdown>` 参数。Skill 层会让 Claude 调 `mcp__arxiv__read_paper` 拿到文本，再把字符串喂回来。这条路子一次 round-trip 多 1–2 秒，但避开了路径耦合。

---

## arxiv MCP 节流

arxiv.org 官方建议 **1 request per 3 seconds**。上游 `blazickjp/arxiv-mcp-server` 的 `download_paper` / `read_paper` 路径**不**做客户端节流（README 声称的限流只覆盖 search），所以并发批量调用 arxiv MCP 极易撞 429 / 被软封 IP（约 60s）。

**关键观察**：`mcp__arxiv__download_paper` 单次调用内部会发**多次 HTTP 请求**——先 probe HTML 元数据，再退回 PDF。这意味着「3s 一次 download_paper」的实际命中频率是 **0.67 req/s**，是 arxiv 限速的 2 倍。当前默认 pace 因此设置为 **6s**：每次 download_paper 内部 ~2 次 HTTP / 6s = ~0.33 req/s，与 arxiv 限速对齐。

**架构限制**：PAI-C MCP 不在 arxiv 调用路径上（Skill 让 Claude 直接调 `mcp__arxiv__*`），无法像 Semantic Scholar 那样 server-side 拦截。节流由 Skill 通过 `mcp__paic__paic_arxiv_pace` 工具自愿协作。

```yaml
providers:
  arxiv:
    batch_size: 1                  # 单消息内允许的并发 arxiv 调用数
    inter_batch_delay_sec: 6.0     # paic_arxiv_pace 默认 sleep 时长（v2 默认）
```

| 字段 | 默认 | 含义 |
|---|---|---|
| `batch_size` | `1` | Skill 提示 Claude 一次最多并发多少篇 arxiv 调用。`1` = 严格串行 |
| `inter_batch_delay_sec` | `6.0` | `paic_arxiv_pace()` 不传参时默认 sleep 时长（秒） |

**档位选择参考**（17 篇 ingest 实测耗时；假设 download_paper 内部 ~2 次 HTTP）：

| `batch_size` | `delay` | 平均速率 | 17 篇耗时 | 429 风险 |
|---|---|---|---|---|
| 1 | **6.0s（默认）** | 0.33 req/s | ~102s | **零**（与官方对齐） |
| 1 | 9.0s | 0.22 req/s | ~153s | 极低（保守余量） |
| 1 | 5.0s | 0.40 req/s | ~85s | 边缘（共享 IP 上仍可能触发） |
| 1 | 3.0s（v1 旧默认） | 0.67 req/s | ~51s | **高**（实测 4 篇就撞 429） |
| 2 | 6.0s | 0.67 req/s | ~51s | 高（同上） |
| 3 | 9.0s | 0.67 req/s | ~34s | 中（依赖 arxiv burst 容忍度） |

简单口诀：**安全区是 (HTTP_PER_CALL × batch_size) / delay ≤ 0.33**。

**`paic_arxiv_pace` 的局限（R20）**：这个工具只是 `time.sleep`，PAI-C 看不到真正的 arxiv 调用。如果 LLM 跳过 pace 调用，节流就失效。SKILL.md 已经把 pace 写进流程，不要主动跳过它。

**429 retry 流程（SKILL 层）**：当 `mcp__arxiv__download_paper` 返回 429，SKILL 应该立刻调 `paic_arxiv_pace(seconds=20)` 长 sleep 一次再重试同一篇；连续两次仍 429 → 让用户决定是 skip 该篇还是手工延长 pace 后继续。

`paic doctor` 会显示当前生效配置：

```text
[OK ]  arxiv pacing                   batch=1 | delay=6.0s | upstream_throttling=unenforced
```

`upstream_throttling=unenforced` 提示用户：本地 pacing 是唯一防线。

---

## Provider

PAI-C 支持 4 个后端，通过 `providers.<vendor>.mode` 切换子模式：

| Backend 全名 | 鉴权来源 | 适用场景 |
|---|---|---|
| `anthropic.api_key` | `ANTHROPIC_API_KEY` | 默认；走官方 SDK，开 prompt caching |
| `anthropic.claude_agent_sdk` | 本机 `claude login` 的 OAuth | **订阅模式**，复用 Claude Code 登录，不耗独立 API credit |
| `openai.api` | `OPENAI_API_KEY` | 官方 OpenAI；走 native JSON mode |
| `openai.compatible` | 看代理；需 `base_url` | OpenRouter / Azure / Ollama / vLLM / LM Studio / 任何 OpenAI 兼容 host |

> ChatGPT Plus / Claude.ai Pro 消费者订阅**不**提供 API access；"订阅模式"在 PAI-C 里特指 `anthropic.claude_agent_sdk` 走本机 Claude Code 登录。

### Anthropic 双路

```yaml
providers:
  anthropic:
    mode: api_key                   # api_key | claude_agent_sdk
    model: claude-opus-4-7
    api_key_env: ANTHROPIC_API_KEY  # 仅 api_key 模式生效
    # base_url: https://mytoken.top # 可选；指向 Anthropic 兼容代理
```

`base_url` 字段（仅 `api_key` 模式生效）让 PAI-C 把 Anthropic 流量送到第三方代理，保留 Anthropic 原生 prompt caching 与 cache_control 支持。`claude_agent_sdk` 模式下此字段被忽略——SDK spawn 本机 `claude` CLI，路由由 CLI 自管。

切到订阅模式：

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk
    model: claude-opus-4-7
```

前置条件：本机已运行过 `claude login`，且 `claude` CLI 在 PATH 上。`paic doctor` 的 "claude CLI on PATH" 一项会校验。

> 运行时碰到 `claude-agent-sdk reported authentication_failed`？先跑 `uv run paic sdk-probe` 拿原始错误，然后查 [troubleshooting.md → claude-agent-sdk reported authentication_failed](troubleshooting.md#claude-agent-sdk-reported-authentication_failed)。`paic doctor --probe` 可以做实活握手提前发现问题。

### OpenAI 双路

```yaml
providers:
  openai:
    mode: api                       # api | compatible
    model: gpt-4o
    api_key_env: OPENAI_API_KEY
    # base_url: https://...         # 仅 compatible 模式必填
```

OpenAI 兼容代理示例：

```yaml
providers:
  openai:
    mode: compatible
    model: gpt-4o-mini
    base_url: https://openrouter.ai/api/v1   # OpenRouter
    # base_url: http://localhost:11434/v1    # Ollama 默认
    # base_url: http://localhost:8000/v1     # vLLM 默认
```

> JSON mode 在不同代理支持度不一；backend 失败会自动 fallback 到通用 ` ```json fence ` 解析。

**第三方中转站（如 mytoken.top）**

国内常见做法：用第三方 API 中转站（mytoken.top / closeai / etc.）拿一个统一 sk-* key，按 OpenAI 兼容协议接 PAI-C。配方：

```powershell
# 1. 把中转站给的 key 设为环境变量（**不要**写进 yaml）
[Environment]::SetEnvironmentVariable("MYTOKEN_API_KEY", "sk-...", "User")
```

```yaml
# 2. ~/.paic/config.yaml
providers:
  openai:
    mode: compatible
    model: gpt-5.5                            # 中转站暴露的 model 别名（按你的 plan 改）
    api_key_env: MYTOKEN_API_KEY              # 自定义 env 名，避免和 OPENAI_API_KEY 撞
    base_url: https://mytoken.top/v1          # OpenAI 兼容路径

routing:
  default: openai                             # 全部 9 个节点走中转站
  # overrides:
  #   summarize: host                         # 想再省钱的话 summarize 走主对话
```

注意点：

- **API key 永远走 env，不进 yaml**：`api_key_env: MYTOKEN_API_KEY` 只是个变量名，PAI-C 启动时去 `os.environ` 拿值。
- **JSON mode 不可靠**：中转站的模型对 `response_format=json_object` 支持度不一致。PAI-C 已有 ```json fence` 解析兜底，但如果某节点（特别是 review 的 4 persona）反复 schema 校验失败，把它单独 override 回 `anthropic.api_key` 或切到 `host`。
- **OpenAI 协议不带 Anthropic prompt caching**：走 OpenAI 兼容协议时，PAI-C 不会发 `cache_control: ephemeral`。4-persona review 每轮都要重发 persona system prompt，token 用量比直连 Anthropic 高约 30%。

**通过 Anthropic 格式接入（可保留 prompt caching）**

如果中转站同时暴露 Anthropic SDK 兼容路径（mytoken.top 的 `https://mytoken.top/` 根路径就是这种），可以直接走 PAI-C 的 `anthropic.api_key` backend，token 与 cache_control 全部保留：

```yaml
providers:
  anthropic:
    mode: api_key
    model: claude-opus-4-7              # 中转站需支持的 Claude 模型名
    api_key_env: MYTOKEN_API_KEY        # 复用同一个 sk-* key 即可
    base_url: https://mytoken.top        # 注意：不带 /v1，Anthropic SDK 会自己拼 /v1/messages

routing:
  default: anthropic
```

什么时候选哪条：

| 你的目标 | 推荐 |
|---|---|
| 模型名是 OpenAI 系（gpt-*）；不在乎 review token 成本 | OpenAI 兼容路径（`openai.compatible` + `/v1`） |
| 模型名是 Anthropic 系（claude-*）；想留 prompt caching 省 review 成本 | Anthropic 格式路径（`anthropic.api_key` + `base_url`） |
| 混合：用便宜 GPT 做 brainstorm + Anthropic 做 review | 两个 provider 都配，用 `routing.overrides` 分流 |

### Semantic Scholar

```yaml
providers:
  semantic_scholar:
    api_key_env: SEMANTIC_SCHOLAR_API_KEY
    base_url: https://api.semanticscholar.org/graph/v1
    timeout_sec: 30.0
    # rate_limit_per_sec: null        # null = 自动 (有 key=0.95, 匿名=0.33)
    # rate_limit_per_sec: 0.95        # 手动指定
```

> S2 官方对认证 key 是 `1 req/sec cumulative across endpoints`。PAI-C 客户端按 0.95 req/s 主动节流（留 5% 安全边际），429 仍发生时再 sleep 2s 兜底重试一次。匿名档默认 0.33 req/s，仍能跑但慢。
>
> **API key 永远不写进 yaml**——`api_key_env` 字段只存环境变量名，由 shell 注入。

`paic doctor` 的 `semantic scholar` 行会显示 `rate=0.95 req/s | key=set`；想看更细的来源（yaml 字面量 / env / legacy 顶层字段），跑 `paic info`。

---

## 路由 routing

每个 LLM 调用站点都有节点标签，router 按 `routing.default + routing.overrides` 分发到具体 backend。

```yaml
routing:
  default: anthropic                 # 简写解析为该 provider 的 mode
  fallback: anthropic.api_key        # 可选；default 抛 LLMUnavailable 时本次起换它
  overrides:
    ideate_brainstorm: openai
    review_persona_reviewer2: anthropic
```

### 9 个节点标签

| 标签 | 站点 | 说明 |
|---|---|---|
| `summarize` | `paic_summarize_run` | 论文结构化摘要 |
| `ideate_brainstorm` | ideate graph | 头脑风暴 N 个 idea |
| `experiment_design` | experiment graph | 生成实验方案 |
| `review_persona_methodology` | review graph | 方法论 reviewer |
| `review_persona_statistics` | review graph | 统计 / 数据 reviewer |
| `review_persona_domain` | review graph | 领域 reviewer |
| `review_persona_reviewer2` | review graph | 创新性 / Reviewer-2 |
| `review_moderator` | review graph | 综合 4 个 critique |
| `review_verdict` | review graph | 终判 accept/reject |
| `draft_polish` | `paic_draft_polish` | 段落 polish（v0.2）—— 可路由到 `host` 走主对话（host orchestration 见下文） |
| `draft_compose` | `paic_draft_compose` | 全 section compose + 引用对齐（v0.3）—— 可路由到 `host` |
| `idea_score_methodology` | ideate panel | feasibility 主审 persona —— 严苛工程师视角 |
| `idea_score_novelty` | ideate panel | novelty 主审 persona —— 文献对照视角 |
| `idea_score_impact` | ideate panel | impact 主审 persona —— 下游受众视角 |
| `idea_score_reviewer2` | ideate panel | reviewer-2 红队 persona —— 强制 ≥2 red_flags / draft |

### 命名规则

- 简写 `anthropic` / `openai`：解析为该 provider 配置的 `mode`
- 全名：`anthropic.api_key` / `anthropic.claude_agent_sdk` / `openai.api` / `openai.compatible`
- **命名 profile**（同 provider 不同 model/key/base_url）：`<profile>` 或 `<profile>.<mode>`——见下一节

### 命名 provider profile（同 provider 不同模型）

`providers.<name>` 下任意非保留键（保留键：`anthropic` / `openai` / `arxiv` / `semantic_scholar` / `external_search`）都是命名 profile，**必须显式给 `kind: anthropic|openai`**。每个 profile 可独立指定 model / mode / api_key_env / base_url。

典型场景：让 ideate 头脑风暴上大模型、experiment 用便宜的 mini、review 走第三方中转：

```yaml
providers:
  openai:                      # 默认 OpenAI profile（保留名）
    mode: api
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY

  openai_pro:                  # 命名 profile：同 key 不同模型
    kind: openai
    mode: api
    model: gpt-5.5
    api_key_env: OPENAI_API_KEY

  openai_or:                   # 命名 profile：换 key + 中转
    kind: openai
    mode: compatible
    model: claude-opus-4-7
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY

routing:
  default: openai                              # 默认 mini，最省
  overrides:
    ideate_brainstorm: openai_pro              # 脑暴用 5.5
    review_persona_methodology: openai_or      # 评审走 OpenRouter
```

引用形式：
- `openai_pro` → 用 profile 默认 mode（这里就是 `api`）
- `openai_pro.api` → 强制 mode（覆盖 profile 的 mode 字段；必须是该 kind 支持的 mode）

`paic doctor` 会为每个命名 profile 输出一行 `profile: <name>`，显示 kind / model / mode / API key 是否就位。

### Fallback 机制

`fallback` 字段可选。若 `default` 抛 `LLMUnavailable`（典型场景：`claude_agent_sdk` 没登录或 token 失效），router 本次起切到 fallback、不中断 graph 流。

推荐组合：`default=anthropic.claude_agent_sdk` 时配 `fallback=anthropic.api_key`——平时走订阅，鉴权失败自动降到 API key 顶住。

### 路由示例：混合策略

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk
    model: claude-opus-4-7
  openai:
    mode: compatible
    base_url: https://openrouter.ai/api/v1
    model: gpt-4o-mini

routing:
  default: anthropic                       # 主用订阅
  fallback: anthropic.api_key              # 订阅鉴权失败降到 API key
  overrides:
    summarize: openai                      # 摘要用便宜小模型
    ideate_brainstorm: openai              # 脑暴也用便宜的
    review_persona_reviewer2: anthropic    # 但最毒的 reviewer 留 Claude
```

跑 `uv run paic info` 验证：

```text
routing default    : anthropic
routing overrides:
  summarize                            → openai
  ideate_brainstorm                    → openai
  review_persona_reviewer2             → anthropic
```

---

## Host Orchestration（订阅复用、零外部 LLM 调用）

`claude_agent_sdk` 已经做到「用 Pro/Max 订阅、零 ANTHROPIC_API_KEY」，但它在 MCP 子进程里 spawn 链路 auth 偶有不稳。**Host orchestration 模式**是更彻底的退路：PAI-C MCP 完全**不发**任何 LLM 调用，让 Claude Code 主对话自己读 markdown、自己生成结构化 JSON，PAI-C 只做 schema 校验 + 写盘。

**适用场景**：
- 你只有 Pro/Max 订阅，不想配 API key、也不想被 `claude_agent_sdk` auth 问题坑
- 你想看到 Claude 的推理过程在主对话里 transparent
- 你已经在为主对话付费（订阅），不想多走一层 SDK 链路

### 支持 host 的节点（白名单）

`host` **只能**在 `routing.overrides` 中给以下节点使用——其他节点 host 无实现，graph 跑到时会抛 `HostOrchestrationRequired` 而**不是**优雅 fallback：

| 节点 | 状态 |
|---|---|
| `summarize` | ✓ 支持 |
| `draft_polish` | ✓ 支持 |
| `draft_compose` | ✓ 支持 |
| `review_persona_*` / `review_moderator` / `review_verdict` | ✗ **不支持**——配置后 review graph 会 crash |
| `ideate_brainstorm` / `idea_score_*` | ✗ **不支持** |
| `experiment_design` | ✗ **不支持** |
| `figure_plan` / `figure_prompt` | ✗ **不支持** |

> Server 启动时**不**校验 `routing.overrides` 里 host 的合法性——配错只在 graph 实际跑到该节点时才崩。所以请仔细按白名单配。`paic doctor` 未来会加这一行校验。

### 配置

`host` 是一个特殊路由目标名，不是 backend。在 `routing.overrides` 里把节点指向它即可：

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk         # 留给 review/ideate/experiment 用
    model: claude-opus-4-7

routing:
  default: anthropic               # → claude_agent_sdk（review 等用）
  overrides:
    summarize: host                # ← 关键：summarize 走主对话
```

**不推荐 `default: host`**——会让所有节点都走 host，包括 review。review graph 是 4 persona × N round 的状态机，host 化会导致体验大幅下降。用 override 显式只覆盖 summarize 这一条。

### 工作流

`/paic-summarize 2401.12345` 在 host 模式下的执行：

1. SKILL 调 `mcp__paic__paic_summarize_run(project_dir, paper_id)`
2. PAI-C 加载 markdown（local probe 或 caller-supplied `paper_text`）
3. PAI-C 检测到 `routing.overrides.summarize: host`，**不**调 LLM；返回：
   ```json
   {
     "mode": "host_orchestration",
     "paper_id": "...",
     "markdown": "<full paper body>",
     "schema_hint": {<JSON schema for problem/method/key_results/limitations/techniques/relevance_to_project>},
     "next_tool": "mcp__paic__paic_summarize_persist",
     "instructions": "..."
   }
   ```
4. SKILL 让 Claude（主对话）读 `markdown`、按 `schema_hint` 生成 JSON
5. SKILL 调 `mcp__paic__paic_summarize_persist(project_dir, paper_id, structured={...})`
6. PAI-C 校验 schema → 写 `library/summaries/<id>.{md,yaml}` → 返回 `persisted: true`

如果 step 5 返回 `error: schema_validation_failed`，SKILL 让 Claude 修正 JSON 重试一次。

`paic doctor` 会显示一行 `host orchestration  enabled for: summarize (no PAI-C-internal LLM call)`。

### 已知约束

- **多篇 summarize 必须串行**：每篇要把整份 markdown 进主对话 context 阅读再生成 JSON。`/paic-summarize all` 17 篇时 SKILL 会逐篇处理（不并发），避免 context 爆。
- **风格一致性**：API 模式有 `prompts/summarize.md` 统一摘要风格；host 模式靠 SKILL.md 内置的英文要求。两者可能轻微漂移。
- **review 不覆盖**：review graph 状态机依赖 PAI-C 内部 LLM 调用 + SqliteSaver checkpoint。host 模式下 review 仍走 `claude_agent_sdk`（或你 default 配置的 backend）。

详见 [troubleshooting.md → host orchestration 排错](troubleshooting.md#host-orchestration-排错)。

---

## 外部检索（多平台 paper-search-mcp）

PAI-C 默认只检索 arXiv + Semantic Scholar。装上游 [`paper-search-mcp`](https://github.com/) 后能扩到 PubMed / bioRxiv / OpenAlex / Crossref / IEEE / SSRN 等 20+ 平台。完整教程见 [`docs/external-search.md`](external-search.md)；这里只列配置 schema。

### 默认（关闭）

```yaml
providers:
  external_search:
    enabled: false
```

`/paic-search` 走 arxiv + Semantic Scholar 双源路径，不调用 paper-search-mcp。

### 开启 + 选研究领域

```yaml
providers:
  external_search:
    enabled: true
    default_preset: biomed                 # 或 cs_ml / physics_math / econ_social /
                                           # engineering / interdisciplinary
    max_results_per_platform: 10
```

`default_preset` 是 `/paic-init` 的兜底值——用户可以在 init 时显式选别的或自定义 platforms。每个 preset 推荐的平台见 [`docs/external-search.md`](external-search.md#适用场景)。

### per-platform 节流（防 429）

```yaml
providers:
  external_search:
    enabled: true
    inter_call_delay_sec:
      pubmed: 0.4         # NCBI 限速保守值
      biorxiv: 1.0
      ssrn: 5.0           # 反爬严
      crossref: 0.05      # polite pool 容得下
```

PAI-C 通过 `paic_search_pace(platform=...)` 工具在 Skill 编排层 voluntary throttling——每个平台请求之间 sleep 配置的时长。完整默认表见 [`docs/external-search.md`](external-search.md#限流-rate-limiting)。

### 项目级 platform 列表

`/paic-init` 会写到 `<project>/.paic/project.yaml`：

```yaml
domain_preset: biomed
platforms:
  - arxiv
  - semantic_scholar
  - pubmed
  - biorxiv
  - europepmc
```

可手编辑。`arxiv` 与 `semantic_scholar` 永远在头部（走老路径，不依赖 paper-search-mcp）。

### 注册 paper-search-mcp

```powershell
# 1) 装上游（替换 <paper-search-mcp> 为你 clone 的实际路径）
cd <paper-search-mcp>
uv sync

# 2) 注册到 Claude Code（在 PAI-C 仓库根目录运行）
cd <PAI-C_REPO>
uv run python scripts/register_paper_search_mcp.py

# 3) 编辑 ~/.paic/config.yaml 把 providers.external_search.enabled 改成 true
# 4) 完全重启 Claude Code
# 5) uv run paic doctor 验证
```

`paic doctor` 会多一行 `external search   enabled=true | preset=... | upstream_throttling=per-platform`。

---

## ideate panel diversification

**问题**：`/paic-ideate` v2 用 4-persona panel（methodology / novelty / impact / reviewer2）评分。如果 4 个 persona 都路由到同一个 backend，评分高度相关——4× 噪声不带 4× 信号。

**`paic doctor` 检测**：会输出一行 `panel routing`：
- `[OK ] panel routing  diverse: 3 distinct backends across 4 personas` —— 配置好
- `[WARN] panel routing  3/4 personas resolve to anthropic.api_key — scoring may be redundant` —— 默认状态，建议改

**推荐配置 1（生产，对照度高）**：
```yaml
routing:
  default: anthropic
  overrides:
    idea_score_reviewer2: openai      # 红队角色路由到不同模型
    idea_score_novelty: anthropic.api_key
    # methodology + impact 用 default
```
- reviewer2 用 OpenAI → 与其它 3 persona 形成 model-level 对照
- 至少 2 distinct backends，doctor 给 OK

**推荐配置 2（低预算）**：
```yaml
providers:
  openai:
    mode: compatible
    model: gpt-4o-mini                 # 便宜中转
    base_url: https://openrouter.ai/api/v1

routing:
  default: anthropic                   # default 给 brainstorm 用（高质量）
  overrides:
    idea_score_methodology: openai
    idea_score_novelty: openai
    idea_score_impact: openai
    idea_score_reviewer2: anthropic    # reviewer2 留 Claude 的最严苛视角
```
- 3/4 persona 用便宜模型，reviewer2 留贵模型 —— 成本砍 ~70%、保留关键红队
- 也通过 doctor 检查（2 distinct backends）

**额外 cost 控制**：
- `personas=["methodology","reviewer2"]` 在 `/paic-ideate` 启动时传 → 2 persona × 3 round ≈ 7-10 LLM calls（vs 默认 12-15）
- ideate v2 内置 score_cache：refine 模式下 keep 的 seed drafts 不重复评分，自动省 25-40% LLM calls
- panel 输出 `max_tokens=1024`（vs default 4096） → 输出 cost 砍 50%

---

## 改完之后

1. **完全退出 Claude Code**（不是 reload，是 quit + reopen）
2. `uv run paic doctor` 全绿
3. `uv run paic info` 看路由长得对不对

任何 routing 配置错误（指向未配置的 provider、未知 backend 名）会在启动期就被 router 拒掉，doctor 输出 `[ERR ] routing` 一行，不会等到第一次调 LLM 才报。
