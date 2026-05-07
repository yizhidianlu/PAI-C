# Configuration Cookbook

> **Cookbook** · 实战配方与决策 —— 给定使用场景挑 backend / 路由 / fallback。字段语义见 [configuration.md](configuration.md)；安装步骤见 [getting-started.md](getting-started.md)。

## 目录

- [选哪种鉴权模式](#选哪种鉴权模式)
- [第三方中转站接入](#第三方中转站接入)
- [Host orchestration 配置](#host-orchestration-配置)
- [ideate panel diversification 实战配置](#ideate-panel-diversification-实战配置)
- [Fallback 与混合策略](#fallback-与混合策略)
- [按场景快速决策](#按场景快速决策)

---

## 选哪种鉴权模式

PAI-C 4 种鉴权模式并非互斥——可以混搭（安装步骤见 [getting-started.md § 4](getting-started.md#4-配置鉴权)）。

| 你的资源 | 选 | 关键配置 |
|---|---|---|
| 只有 `ANTHROPIC_API_KEY`（按 token 付费） | **4A** API Key 模式（默认） | `providers.anthropic.mode: api_key` |
| 有 Pro/Max 订阅 + 已 `claude login` | **4B** Subscription 模式（零 API 费） | `providers.anthropic.mode: claude_agent_sdk` |
| 有 Pro/Max 订阅 + 想绕开 SDK 鉴权风险 | **4C** Subscription + Host orchestration | 在 4B 上加 `routing.overrides.summarize: host` |
| 用第三方中转站 / OpenRouter / Ollama / vLLM | **4D** OpenAI 兼容模式 | `providers.openai.mode: compatible` + `base_url` |
| 既有 API key 又有 Pro 订阅 | **4B + Fallback** | `default: anthropic` + `fallback: anthropic.api_key` |

### 取舍要点

- **prompt caching** 只在 `anthropic.api_key` 模式生效（含「Anthropic 协议接入」中转站）；`claude_agent_sdk` 与 `openai.compatible` **不带 caching**。多轮 review 时输入 token 成本差 5–10×。
- **JSON mode 可靠性**：Anthropic native > OpenAI native > 第三方中转站。中转站对 `response_format=json_object` 支持参差；PAI-C 有 ```json fence` 兜底解析，但 review panel 反复 schema 失败时把该节点单独 override 回 `anthropic.api_key`。
- **不要让 `routing.default` 直接走 `claude_agent_sdk`**：MCP 子进程并发 spawn SDK 客户端易撞 auth race；推荐 `default: anthropic`（解析为 mode）+ `fallback: anthropic.api_key`。

---

## 第三方中转站接入

国内常见做法：用第三方中转站（mytoken.top / closeai 等）拿一个统一 sk-* key。**API key 永远走环境变量，不写进 yaml**。

### 路径 A：OpenAI 协议（gpt-* 模型 / OpenRouter / Ollama）

```powershell
# 1. 把中转站给的 key 设为环境变量
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
  default: openai                             # 全部节点走中转站
  # overrides:
  #   summarize: host                         # 想再省钱的话 summarize 走主对话
```

注意点：
- **JSON mode 不可靠**：见上方「取舍要点」。
- **不带 Anthropic prompt caching**：4-persona review 每轮都要重发 persona system prompt，token 用量比直连 Anthropic 高约 30%。

### 路径 B：Anthropic 协议（claude-* 模型 / 保留 caching）

如果中转站同时暴露 Anthropic SDK 兼容路径（mytoken.top 的根路径就是这种），可以直接走 `anthropic.api_key` backend，token 与 cache_control 全部保留：

```yaml
providers:
  anthropic:
    mode: api_key
    model: claude-opus-4-7              # 中转站需支持的 Claude 模型名
    api_key_env: MYTOKEN_API_KEY        # 复用同一个 sk-* key 即可
    base_url: https://mytoken.top        # 注意：不带 /v1，Anthropic SDK 自己拼 /v1/messages

routing:
  default: anthropic
```

### 什么时候选哪条

| 你的目标 | 推荐 |
|---|---|
| 模型名是 OpenAI 系（gpt-*）；不在乎 review token 成本 | 路径 A（`openai.compatible` + `/v1`） |
| 模型名是 Anthropic 系（claude-*）；想留 prompt caching 省 review 成本 | 路径 B（`anthropic.api_key` + `base_url`） |
| 混合：用便宜 GPT 做 brainstorm + Anthropic 做 review | 两个 provider 都配，用 `routing.overrides` 分流（见下方混合策略） |

---

## Host orchestration 配置

> Host orchestration 让 PAI-C 完全**不发**任何 LLM 调用，由 Claude Code 主对话执行；零 API 风险。directive schema / in-graph flow / 实现新 host-aware 节点的开发者细节见 [host-orchestration-internals.md](host-orchestration-internals.md)。

### 何时用

- 只有 Pro/Max 订阅，不想被 `claude_agent_sdk` 在 MCP 子进程里 auth_failed 风险坑
- 想看到 Claude 的推理过程在主对话里 transparent
- 已经为主对话付费，不想多走一层 SDK 链路

### 支持节点

`host` 在 `routing.overrides` 或 `routing.default` 都可用。**全部 19 个 LLM 节点**都已 host-aware（详见 [configuration.md § 节点路由表](configuration.md#节点路由表)的「是否 host-aware」列）。

> ⚠️ **不要 `default: host`**：会让所有节点走 host，包括 review/ideate 等多轮 graph，体验大幅下降（每个 persona 都要 round-trip 主对话）。用 `overrides` 显式覆盖。

### 配置示例

最小推荐配置——只让 summarize 走主对话，其他保持订阅：

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk        # 给 review/ideate 等用
    model: claude-opus-4-7

routing:
  default: anthropic              # → claude_agent_sdk
  overrides:
    summarize: host               # ← 关键：summarize 走主对话
```

更激进的零 API 配置（叠加 paragraph_write 之外的整稿合成 / 润色）：

```yaml
routing:
  default: anthropic
  overrides:
    summarize: host
    draft_compose: host           # 整稿合成走主对话
    draft_polish: host            # 整稿润色也走主对话
```

### 工作流示意

**Sync MCP 节点**（`summarize` / `claim_extract` / `relwork_cluster` / `revision_extract` / `paper_plan_generate` / `figure_plan` / `figure_prompt` / `draft_polish` / `draft_compose`）：

```
SKILL → paic_<name>_run
        → returns directive (mode=host_orchestration, schema_hint=...)
   ← host LLM produces JSON matching schema_hint
     paic_<name>_persist(structured=<JSON>)
        → writes output on disk
```

**In-graph 节点**（review / ideate / experiment graph 内部节点）：

```
paic_<graph>_start → graph runs until first host-routed node
   → interrupt({"stage": "host_orchestration", "directive": {...}})
   ← host LLM produces JSON
     paic_<graph>_step(host_response=<JSON>)
   → graph resumes to next interrupt or end
```

`paic doctor` 会显示 `host orchestration enabled for: <node list>`。

---

## ideate panel diversification 实战配置

> 原理与诊断（为什么单 backend 评分高度相关、`paic doctor` 怎么报告）见 [configuration.md § ideate panel diversification](configuration.md#ideate-panel-diversification)。

### 推荐配置 1（生产，对照度高）

```yaml
routing:
  default: anthropic
  overrides:
    idea_score_reviewer2: openai      # 红队角色路由到不同模型
    idea_score_novelty: anthropic.api_key
    # methodology + impact 用 default
```

reviewer2 走 OpenAI → 与其它 3 persona 形成 model-level 对照；至少 2 distinct backends，doctor 给 OK。

### 推荐配置 2（低预算）

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

3/4 persona 用便宜模型，reviewer2 留贵模型 —— 成本砍 ~70%、保留关键红队。也通过 doctor 检查（2 distinct backends）。

### 额外 cost 控制

- `personas=["methodology","reviewer2"]` 在 `/paic-ideate` 启动时传 → 2 persona × 3 round ≈ 7-10 LLM calls（vs 默认 12-15）
- ideate v2 内置 score_cache：refine 模式下 keep 的 seed drafts 不重复评分，自动省 25-40% LLM calls
- panel 输出 `max_tokens=1024`（vs default 4096） → 输出 cost 砍 50%

更系统的 model-tier 推荐见 [model-presets.md](model-presets.md)。

---

## Fallback 与混合策略

### Fallback 机制

`routing.fallback` 可选。若 `default` 抛 `LLMUnavailable`（典型场景：`claude_agent_sdk` 没登录或 token 失效），router 本次起切到 fallback、不中断 graph 流。

> ⚠️ **`fallback: host` 被 router 拒绝**——host 是用户主动选择的路由目标，不是 transient failure 的兜底。

推荐组合：

```yaml
routing:
  default: anthropic.claude_agent_sdk
  fallback: anthropic.api_key                # SDK 鉴权失败自动降级
```

平时走订阅，鉴权失败自动降到 API key 顶住，不需要重启 Claude Code。

### 混合策略示例

不同节点走不同 backend：

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

## 按场景快速决策

| 场景 | 推荐配置 | 文档 |
|---|---|---|
| **个人投稿，按 token 付费** | 4A + 全 Opus | [model-presets.md § Premium](model-presets.md#档位-a--premium质量优先) |
| **日常多轮迭代** | 4A + Balanced 分级 | [model-presets.md § Balanced](model-presets.md#档位-b--balanced推荐默认) |
| **学生党 / Pro 订阅** | 4B + 4C host orch + Frugal 分级 | [model-presets.md § Frugal](model-presets.md#档位-c--frugal订阅--中转零或近零-api-费) |
| **国内用户 / 中转站** | 4D（Anthropic 协议路径优先） | 上方 [第三方中转站接入](#第三方中转站接入) |
| **混合：贵的留 Claude，便宜的中转** | `default: anthropic` + `overrides` 分流 | 上方 [混合策略示例](#混合策略示例) |
| **panel 评分总趋同** | 红队 persona 单独 override 跨 backend | 上方 [ideate panel diversification 实战配置](#ideate-panel-diversification-实战配置) |
| **零 API 费 + 零 SDK 风险** | 4C summarize/draft_compose/draft_polish 全 host | 上方 [Host orchestration 配置](#host-orchestration-配置) |

---

> 改完 `~/.paic/config.yaml` 必须**完全重启 Claude Code**（MCP server 不热加载），跑 `uv run paic info` 验证生效路由、`uv run paic doctor` 确认全绿。
