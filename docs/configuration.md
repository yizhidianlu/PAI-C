# Configuration 参考

> **Reference** · `~/.paic/config.yaml` 全字段 —— 4 个 LLM backend / 命名 profile / host orchestration / 节流 / 多平台检索 / ideate panel diversification。

## 目录

- [顶层字段](#顶层字段)
- [arxiv 存储路径](#arxiv-存储路径) · [arxiv MCP 节流](#arxiv-mcp-节流)
- [Provider](#provider) · [Anthropic 双路](#anthropic-双路) · [OpenAI 双路](#openai-双路) · [Semantic Scholar](#semantic-scholar)
- [路由 routing](#路由-routing) · [节点路由表](#节点路由表) · [命名 provider profile](#命名-provider-profile同-provider-不同模型)
- [Host Orchestration（基础）](#host-orchestration)
- [外部检索（多平台 paper-search-mcp）](#外部检索多平台-paper-search-mcp)
- [ideate panel diversification（原理与检测）](#ideate-panel-diversification)
- [改完之后](#改完之后)

> **实战配方与决策树**（4 鉴权模式 / 第三方中转 / host 全套 yaml / panel 推荐配置 / fallback 与混合策略）见 [configuration-cookbook.md](configuration-cookbook.md)。

---

`register_mcp.py` 首次运行时会从 [`config.yaml.example`](config.yaml.example) seed 一份到 `~/.paic/config.yaml`。**已存在则不覆盖**，所以你之后改的不会被脚本擦掉。随时可跑 `uv run paic info` 看当前生效的解析结果，或 `uv run paic doctor` 验证整体健康。

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

**第三方中转站（mytoken.top / OpenRouter / closeai）**：可走两条路径——OpenAI 协议（`openai.compatible` + `/v1`，gpt-* 模型用）或 Anthropic 协议（`anthropic.api_key` + `base_url`，claude-* 模型用，**保留 prompt caching**）。完整 yaml 模板与决策表见 [configuration-cookbook.md § 第三方中转站接入](configuration-cookbook.md#第三方中转站接入)。

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

### 节点路由表

PAI-C 共 **25 个 LLM 调用节点**（其中 21 个支持 host orchestration）。每个 `routing.overrides.<node>` 必须填下表中的 `节点` 列；按 workload 维度（max_tokens / temperature / 调用频次）选模型见 [model-presets.md](model-presets.md)。

| 节点 | 触发命令 | 简述 | host-aware |
|---|---|---|:---:|
| `summarize` | `/paic-summarize` | 论文结构化摘要 | ✓ |
| `paper_plan_generate` | `/paic-paper-plan` | 全局 paper plan 生成 | ✓ |
| `claim_extract` | `paic_claims_extract` | 抽取论文主张 | ✓ |
| `claim_judge` | `paic_claims_validate` | 三分类 supports/contradicts/neutral | — |
| `relwork_cluster` | `paic_related_work_cluster` | 相关工作聚类 | ✓ |
| `revision_extract` | `paic_revision_extract` | 审稿意见抽取 | ✓ |
| `ideate_brainstorm` | `/paic-ideate` | 头脑风暴 N 个 idea | ✓ (in-graph) |
| `idea_score_methodology` | `/paic-ideate` panel | 严苛工程师 persona | ✓ (in-graph) |
| `idea_score_novelty` | `/paic-ideate` panel | 文献对照 persona | ✓ (in-graph) |
| `idea_score_impact` | `/paic-ideate` panel | 下游受众 persona | ✓ (in-graph) |
| `idea_score_reviewer2` | `/paic-ideate` panel | 红队 persona —— 强制 ≥2 red_flags | ✓ (in-graph) |
| `experiment_design` | `/paic-experiment` | 实验方案设计 | ✓ (in-graph) |
| `review_persona_methodology` | `/paic-review` | 方法论 reviewer | ✓ (in-graph) |
| `review_persona_statistics` | `/paic-review` | 统计 / 数据 reviewer | ✓ (in-graph) |
| `review_persona_domain` | `/paic-review` | 领域 reviewer | ✓ (in-graph) |
| `review_persona_reviewer2` | `/paic-review` | 创新性 / Reviewer-2 | ✓ (in-graph) |
| `review_moderator` | `/paic-review` | 综合 4 个 critique | ✓ (in-graph) |
| `review_verdict` | `/paic-review` | 终判 accept/revise/reject | ✓ (in-graph) |
| `paragraph_outline` | `/paic-draft compose` | 段落级 outline 生成 | — |
| `paragraph_write` | `/paic-draft compose` | 单段 LaTeX 写作（高频） | — |
| `section_coherence_polish` | `/paic-draft compose` | 节内连贯性润色 | — |
| `draft_compose` | `/paic-draft compose` | 全 section 整稿合成 | ✓ |
| `draft_polish` | `/paic-draft polish` | 段落 polish | ✓ |
| `figure_plan` | `/paic-figure plan` | 配图规划（≤4 张） | ✓ |
| `figure_prompt` | `/paic-figure generate` | image-gen prompt 写作 | ✓ |

> **`host-aware`** 列含义：✓ 标记的节点支持 `routing.overrides.<node>: host`，调用时返回 host directive 让主对话生成结构化输出；— 标记的节点直接调云端 LLM，路由到 `host` 会触发 `HostOrchestrationRequired` 错误（高频窄任务，host round-trip 开销不划算）。`paic doctor` 启动时会校验 `routing.overrides` 是否合法，错配在跑流水线前就报。
>
> **`(in-graph)`** 标记的节点跑在 LangGraph 状态机内（review / ideate / experiment），host 化后通过 `interrupt(...)` 暂停 graph，调用方按 `*_step(host_response=...)` 推进。详见 [host-orchestration-internals.md](host-orchestration-internals.md)。

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

### 案例：用命名 profile 做 panel diversity（reviewer2 红队）

PAI-C ideate 的 4-persona 评分（feasibility / novelty / impact / reviewer2）默认全走 `routing.default`——同一个 backend 同一个 model，4 个 persona 容易给出**高度相关**的分数（reviewer2 应有的"挑刺"角色被稀释）。

实测有效的做法：把 `idea_score_reviewer2` 单独路由到一个**不同 model 系列**的命名 profile：

```yaml
providers:
  openai:
    mode: api
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY

  openai_pro:                  # reviewer2 专用：换更大 model
    kind: openai
    mode: api
    model: gpt-5.5            # 或换成 anthropic.api_key + claude-opus-4-7
    api_key_env: OPENAI_API_KEY

routing:
  default: openai                                # 其他 3 个 persona 走 mini
  overrides:
    idea_score_reviewer2: openai_pro             # reviewer2 走 5.5
```

为什么有效：跨 model 系列的 prompt-following 偏差天然不同。模拟实测一次 ideate 的分布：

| persona | feasibility | novelty | impact | avg | backend |
|---|---|---|---|---|---|
| methodology | 0.49 | 0.64 | 0.55 | 0.56 | gpt-5.4-mini |
| novelty | 0.66 | 0.56 | 0.58 | 0.60 | gpt-5.4-mini |
| impact | 0.56 | 0.81 | 0.57 | 0.65 | gpt-5.4-mini |
| **reviewer2** | **0.39** | **0.35** | **0.36** | **0.37** | **gpt-5.5（隔离 backend）** |

`panel_consensus` 字段给出 `"diverged"`，符合红队设计目的（其他 3 persona consensus 落 0.56-0.65 区间，reviewer2 系统性给低分）。如果 reviewer2 也走 `default`，4 个 persona 的 avg 通常落在 ±0.05 内——consensus 永远是 `"agreed"`，挑刺意图无效。

类似策略对其他需要 diversity 的节点也适用：
- `review_persona_reviewer2` — 同一道理，跨 model 系列拉开 critique
- 不同 `review_persona_*`（methodology / statistics / domain）混用 mini + pro 拉开覆盖角度（成本依然可控）

不要做：把 4 个 persona 全都 override 到同一个 `openai_pro` —— 这只是把"同 backend"的问题搬到更贵的 backend 上，diversity 没变化、成本翻倍。**只在挑刺型 persona 上隔离 backend** 性价比最高。

### Fallback 机制

`fallback` 字段可选。若 `default` 抛 `LLMUnavailable`（典型场景：`claude_agent_sdk` 没登录或 token 失效），router 本次起切到 fallback、不中断 graph 流。`fallback: host` 被 router 拒绝——host 是用户主动选择的路由目标，不是 transient failure 的兜底。

混合策略 yaml 模板（订阅 + 中转站分流 / 红队保 Claude / Fallback 链组合）见 [configuration-cookbook.md § Fallback 与混合策略](configuration-cookbook.md#fallback-与混合策略)。

---

## Host Orchestration

`host` 是一个特殊路由目标——PAI-C MCP 完全**不发**任何 LLM 调用，让 Claude Code 主对话生成结构化 JSON，PAI-C 只做 schema 校验 + 写盘。这是「订阅复用 + 零外部 LLM 调用」的最彻底退路。

**支持节点**：25 个 LLM 节点中 21 个支持 host orchestration（4 个高频窄任务节点 — `claim_judge` / `paragraph_outline` / `paragraph_write` / `section_coherence_polish` — 不支持，详见上方 [节点路由表](#节点路由表) 「host-aware」列）。`host` 在 `routing.overrides` 与 `routing.default` 都可用，但**不要 `default: host`**——会让所有节点走主对话 round-trip，多轮 graph 体验大幅下降。

**最小配置**：

```yaml
routing:
  default: anthropic
  overrides:
    summarize: host                # 摘要走主对话；零外部 LLM
```

- 配置场景与 yaml 模板（含 sync flow / in-graph flow 工作流图）见 [configuration-cookbook.md § Host orchestration 配置](configuration-cookbook.md#host-orchestration-配置)
- directive schema / failure modes / 实现新 host-aware 节点见 [host-orchestration-internals.md](host-orchestration-internals.md)
- 错误恢复见 [troubleshooting.md → host orchestration 排错](troubleshooting.md#host-orchestration-排错)

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

**实战 yaml 模板**（生产对照度配置 / 低预算配置 / cost 控制）见 [configuration-cookbook.md § ideate panel diversification 实战配置](configuration-cookbook.md#ideate-panel-diversification-实战配置)；按 model-tier 系统化的推荐见 [model-presets.md](model-presets.md)。

---

## 改完之后

1. **完全退出 Claude Code**（不是 reload，是 quit + reopen）
2. `uv run paic doctor` 全绿
3. `uv run paic info` 看路由长得对不对

任何 routing 配置错误（指向未配置的 provider、未知 backend 名）会在启动期就被 router 拒掉，doctor 输出 `[ERR ] routing` 一行，不会等到第一次调 LLM 才报。
