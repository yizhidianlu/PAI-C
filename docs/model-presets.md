# 模型分级配置

> **Reference** · 按 pipeline 各节点的 workload（输入规模 / 推理性质 / 调用频次 / temperature / max_tokens）给出三档（Premium / Balanced / Frugal）模型推荐 + 可直接粘贴的 `~/.paic/config.yaml` 模板。Backend 机制（命名 profile / fallback / host orchestration / panel diversification）见 [configuration.md](configuration.md)。

## 目录

- [节点 workload 总览](#节点-workload-总览)
- [三个判断维度](#三个判断维度)
- [档位 A · Premium](#档位-a--premium质量优先)
- [档位 B · Balanced（推荐默认）](#档位-b--balanced推荐默认)
- [档位 C · Frugal](#档位-c--frugal订阅--中转零或近零-api-费)
- [对应 yaml 模板](#对应-yaml-模板直接粘贴)
- [何时升档 / 降档](#何时升档--降档)

---

## 节点 workload 总览

> 节点用法 / host-aware 边界 / 路由命名规则见 [configuration.md § 节点路由表](configuration.md#节点路由表)；本表按 workload 维度（max_tokens / temperature / 调用频次 / 推理性质）细分，决定**配多大模型**。

每个 LLM 调用站点都有节点标签，router 按 `routing.default + routing.overrides` 分发到具体 backend。

| 节点 | 触发命令 | max_tokens | temp | 输入规模 | 推理性质 | 调用频次 |
|---|---|---:|---:|---|---|---|
| `summarize` | `/paic-summarize` | 2048 | 0.1 | 长（单篇全文 10–30K tok） | 抽取 + 严格 JSON schema | N 篇/批 |
| `ideate_brainstorm` | `/paic-ideate` | 4096 | 0.6 | 中（多篇摘要） | 创造性综合，长输出 | 每轮 1 |
| `idea_score_{methodology,novelty,impact,reviewer2}` | `/paic-ideate` panel | 1024 | 0.1–0.5 | 短 | persona 评分；diversity 关键 | 4×N 每轮 |
| `paper_plan_generate` | `/paic-paper-plan` | 4096 | 0.2 | 中 | 全局架构设计；下游全依赖 | 1–2/项目 |
| `experiment_design` | `/paic-experiment` | 4096 | 0.2 | 中 | 严密领域推理 | 1/idea |
| `review_persona_{methodology,statistics,domain,reviewer2}` | `/paic-review` | 2048 | 0.3 | 中 | 批判性领域 critique；diversity 关键 | 4×rounds |
| `review_moderator` | `/paic-review` | 2048 | 0.2 | 中（4 persona 输出聚合） | 综合仲裁 | 1/round |
| `review_verdict` | `/paic-review` | 2048 | 0.1 | 中 | 决策性 | 终局 1 |
| `claim_extract` | `paic_claims_extract` | 2048 | 0.1 | 中 | 抽取式 | 1/篇 |
| `claim_judge` | `paic_claims_validate` | 512 | 0.0 | 短 | 三分类 supports/contradicts/neutral | **高频** N×M |
| `relwork_cluster` | `paic_related_work_cluster` | 2400 | 0.2 | 长（多篇摘要拼接） | 分类 + 命名 | 1–2/项目 |
| `revision_extract` | `paic_revision_extract` | 2400 | 0.2 | 中 | 抽取式 | 1/审稿包 |
| `paragraph_outline` | `/paic-draft compose` | 2048 | 0.2 | 短 | 结构化规划 | 1/section |
| `paragraph_write` | `/paic-draft compose` | 900 | 0.3 | 中（含 ref ctx） | 单段写作 + 引用对齐 | **高频** 30–60/项目 |
| `section_coherence_polish` | `/paic-draft compose` | 2400 | 0.2 | 长（整 section） | 风格统一 | 1/section |
| `draft_compose` | `/paic-draft compose` | 4096 | 0.4 | 长 | 长输出 + 全局协调 | 1–2/项目 |
| `draft_polish` | `/paic-draft polish` | 4096 | 0.3 | 长 | 风格 + 准确性 rewrite | 按需 |
| `figure_plan` | `/paic-figure plan` | 2048 | 0.3 | 中（draft 全文） | 视觉规划 | 1/项目 |
| `figure_prompt` | `/paic-figure generate` | 512 | 0.4 | 短 | image-gen prompt 工程 | 1/figure |

---

## 三个判断维度

下面三档推荐都按这三条决策的：

1. **长上下文要不要**：`summarize` / `relwork_cluster` / `draft_*` / `section_coherence_polish` 吃 ≥ 30K tok 输入。Sonnet / Haiku / GPT-5.4-mini 上下文够用，但 schema-heavy 节点上**指令遵循劣化**——宁可大模型一次成功，也别小模型重试。
2. **Diversity 是不是硬约束**：`idea_score_reviewer2` / `review_persona_reviewer2` 红队 persona 跟其他 persona 用**同一 backend** 时打分高度相关（实测 r > 0.85），多样性退化。这两个节点必须**跨 backend** 路由。机制见 [configuration.md § ideate panel diversification](configuration.md#ideate-panel-diversification)。
3. **Caching 红利**：`anthropic.api_key` 模式下 Anthropic prompt caching 自动生效，review / ideate / draft 反复带同一 system prompt + library context 时**输入成本 -90%**。OpenAI 兼容中转站、`claude_agent_sdk` 都不带 caching；选 backend 时如果两选项质量差不多，优先有 caching 的那条。中转站走 Anthropic 协议保留 caching 的写法见 [configuration-cookbook.md § 第三方中转站接入](configuration-cookbook.md#第三方中转站接入)。

> 📊 **写作链不要降级**：`paragraph_write` / `section_coherence_polish` / `draft_compose` 是读者最终读到的文笔。这三个节点降到 Sonnet 以下，质量差距人眼可辨，其他节点都可以省、这三个不行。

---

## 档位 A · Premium（质量优先）

适用：投稿前最终稿 / 重要会议 deadline / 关键综述。预估账单：单稿全流水线 **~$30–60**。

| 节点 | 推荐 | 理由 |
|---|---|---|
| `summarize` | **Opus 4.7** (`anthropic.api_key`) | 长上下文最稳；caching 红利最大 |
| `ideate_brainstorm` | **Opus 4.7** | 创造性 + 长输出，决定后续整条流水线 |
| `idea_score_methodology` / `novelty` / `impact` | **Opus 4.7** | panel 主体 |
| `idea_score_reviewer2` | **GPT-5.5** (`openai.api`) | **强制跨 backend** 拿 diversity；GPT-5.5 与 Opus 在统计上独立 |
| `paper_plan_generate` | **Opus 4.7** | 项目地基，错一次全局返工 |
| `experiment_design` | **Opus 4.7** | 严密推理 + 领域常识 |
| `review_persona_methodology` / `statistics` / `domain` | **Opus 4.7** | 主体 panel |
| `review_persona_reviewer2` | **GPT-5.5** | 同 idea panel，diversity 硬约束 |
| `review_moderator` / `review_verdict` | **Opus 4.7** | 仲裁 / 决议要全局视角 |
| `claim_extract` / `revision_extract` / `relwork_cluster` | **Opus 4.7** | 抽取错一处下游全错 |
| `claim_judge` | **Sonnet 4.6** | 高频任务简单；Opus 在三分类上无显著 lift，不值差价 |
| `paragraph_outline` / `paragraph_write` / `section_coherence_polish` | **Opus 4.7** | 文笔决定可读性，最终人工读得到的就这层 |
| `draft_compose` / `draft_polish` | **Opus 4.7** | 长输出 + 全局协调 |
| `figure_plan` | **Opus 4.7** | 一次性，无所谓贵 |
| `figure_prompt` | **Sonnet 4.6** | image-gen prompt 短任务，Opus 浪费 |

---

## 档位 B · Balanced（推荐默认）

适用：日常实验 / 多轮 ideate-experiment-review 迭代 / 草稿期。预估账单：单稿全流水线 **~$8–18**。

策略：长上下文 / 全局节点用 Opus，高频段写用 Sonnet，diversity 节点跨 backend，简单分类用 Haiku。

| 节点 | 推荐 | 理由 |
|---|---|---|
| `summarize` | **Opus 4.7** | 长上下文质量差距大 |
| `ideate_brainstorm` | **Opus 4.7** | 头脑风暴一次性，差不起 |
| `idea_score_methodology` / `novelty` / `impact` | **Sonnet 4.6** | panel 4 次/轮；Sonnet ≈ Opus 92% 质量、价格 1/5 |
| `idea_score_reviewer2` | **GPT-5.4** (`openai.api`) | diversity；GPT-5.4 比 Opus 便宜 50% |
| `paper_plan_generate` | **Opus 4.7** | 地基不能省 |
| `experiment_design` | **Opus 4.7** | 同上 |
| `review_persona_methodology` / `statistics` / `domain` | **Sonnet 4.6** | 多轮也省得多 |
| `review_persona_reviewer2` | **GPT-5.4** | diversity |
| `review_moderator` | **Sonnet 4.6** | 综合任务 Sonnet 够 |
| `review_verdict` | **Opus 4.7** | 终局决策值得贵 |
| `claim_extract` | **Sonnet 4.6** | 抽取式 |
| `claim_judge` | **Haiku 4.5** | **高频三分类，Haiku 准确率 ~95% Opus，价格 1/30** |
| `relwork_cluster` | **Opus 4.7** | 一次性，决定 related work 章节结构 |
| `revision_extract` | **Sonnet 4.6** | 抽取式 |
| `paragraph_outline` | **Sonnet 4.6** | 结构化规划，Sonnet 够 |
| `paragraph_write` | **Opus 4.7** | 核心写作，30–60 段加起来才几刀 |
| `section_coherence_polish` | **Opus 4.7** | 节内连贯性差距明显 |
| `draft_compose` | **Opus 4.7** | 整稿合成 |
| `draft_polish` | **Sonnet 4.6** | polish 是迭代式，多跑两次也不贵 |
| `figure_plan` | **Sonnet 4.6** | 一次性 |
| `figure_prompt` | **Haiku 4.5** | prompt 工程小任务 |

> ⚠️ **保留 prompt caching**：所有 anthropic 节点用 `mode: api_key`（不是 `claude_agent_sdk`），review / ideate / draft 多次跑 → 输入成本 -90%。

---

## 档位 C · Frugal（订阅 + 中转，零或近零 API 费）

适用：学生党 / 长期项目 / 已有 Pro/Max 订阅 + 自部署 vLLM。预估账单：单稿 **~$0–3**（取决于 DeepSeek/Qwen 用量）。

策略：能 host orchestration 让主对话做就让主对话做（21 个 host-aware 节点见 [configuration.md § 节点路由表](configuration.md#节点路由表) 「host-aware」列；用户配置见 [configuration-cookbook.md § Host orchestration 配置](configuration-cookbook.md#host-orchestration-配置)）；剩下的全压第三方中转 / 自部署。

| 节点 | 推荐 | 理由 |
|---|---|---|
| `summarize` | **`host`** | 摘要直接由 Claude Code 主对话做，**零 SDK / API 费** |
| `ideate_brainstorm` | **DeepSeek-V3** (`openai.compatible` + 中转) | 中文理解 + 创造性强；价格 ~$0.27/M tok（Opus 1/50） |
| `idea_score_methodology` / `novelty` / `impact` | **Qwen3-72B**（自部署 vLLM 或开源 cloud） | 高频任务自部署 0 边际成本 |
| `idea_score_reviewer2` | **Sonnet 4.6** (`anthropic.api_key`) | 哪怕全省也要保 diversity——红队走 Anthropic 才跟 DeepSeek/Qwen 真正独立 |
| `paper_plan_generate` | **`claude_agent_sdk`** (Pro/Max 订阅) | 一次性，订阅族零边际成本 |
| `experiment_design` | **`claude_agent_sdk`** | 同上 |
| `review_persona_methodology` / `statistics` / `domain` | **DeepSeek-V3** | DeepSeek 主观质量 ≈ Sonnet 80% |
| `review_persona_reviewer2` | **`claude_agent_sdk`** | diversity，订阅族顺带跑 |
| `review_moderator` / `review_verdict` | **`claude_agent_sdk`** | 一次性 |
| `claim_extract` / `revision_extract` | **Qwen3-72B** | 抽取式开源够 |
| `claim_judge` | **Llama-4-Maverick** 或本地 Qwen3-32B | 高频三分类，自部署最划算 |
| `relwork_cluster` | **DeepSeek-V3** | 一次性，DeepSeek 中文聚类好 |
| `paragraph_outline` | **Qwen3-72B** | 结构化规划够用 |
| `paragraph_write` | **`claude_agent_sdk`** | **唯一不退让**——文笔差距明显，订阅免费打 |
| `section_coherence_polish` | **`claude_agent_sdk`** | 同上 |
| `draft_compose` | **`host`** | 整稿合成让主对话做，零 SDK 风险 + 零费 |
| `draft_polish` | **`host`** | 同上 |
| `figure_plan` | **DeepSeek-V3** | 一次性 |
| `figure_prompt` | **Qwen3-32B** (本地 Ollama 也行) | 短任务，本地 0 成本 |

> ⚠️ **`host` 节点边界**：21 个 host-aware 节点之外，4 个高频窄任务节点（`claim_judge` / `paragraph_outline` / `paragraph_write` / `section_coherence_polish`）配 `host` 会触发 `HostOrchestrationRequired`。完整边界见 [configuration.md § 节点路由表](configuration.md#节点路由表)；`paic doctor` 启动时会拒掉非法 host override。
>
> ⚠️ **`claude_agent_sdk` 无 prompt caching**——多轮 review 比 `api_key` 模式贵 5–10×（system prompt 每次重传）。订阅党对成本钝感所以无所谓，但走 API 的人要对比账单。

---

## 对应 yaml 模板（直接粘贴）

> 改完 `~/.paic/config.yaml` 必须**完全重启 Claude Code**，MCP server 不会热加载。跑 `uv run paic info` 验证生效路由。

### 档位 A · Premium

```yaml
providers:
  anthropic:
    mode: api_key
    model: claude-opus-4-7
    api_key_env: ANTHROPIC_API_KEY

  openai:
    mode: api
    model: gpt-5.5
    api_key_env: OPENAI_API_KEY

  anth_sonnet:
    kind: anthropic
    mode: api_key
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY

routing:
  default: anthropic                                  # = Opus 4.7
  fallback: anthropic.api_key
  overrides:
    # diversity 红队
    idea_score_reviewer2: openai
    review_persona_reviewer2: openai

    # 简单分类降 Sonnet
    claim_judge: anth_sonnet
    figure_prompt: anth_sonnet
```

### 档位 B · Balanced（推荐默认）

```yaml
providers:
  anthropic:
    mode: api_key
    model: claude-opus-4-7
    api_key_env: ANTHROPIC_API_KEY

  openai:
    mode: api
    model: gpt-5.4
    api_key_env: OPENAI_API_KEY

  anth_sonnet:
    kind: anthropic
    mode: api_key
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY

  anth_haiku:
    kind: anthropic
    mode: api_key
    model: claude-haiku-4-5-20251001
    api_key_env: ANTHROPIC_API_KEY

routing:
  default: anthropic                                  # = Opus 4.7
  fallback: anthropic.api_key
  overrides:
    # ideate panel：3 主体走 Sonnet，红队走 GPT 拿 diversity
    idea_score_methodology: anth_sonnet
    idea_score_novelty: anth_sonnet
    idea_score_impact: anth_sonnet
    idea_score_reviewer2: openai

    # review panel：同上
    review_persona_methodology: anth_sonnet
    review_persona_statistics: anth_sonnet
    review_persona_domain: anth_sonnet
    review_persona_reviewer2: openai
    review_moderator: anth_sonnet

    # 抽取 / 分类
    claim_extract: anth_sonnet
    claim_judge: anth_haiku
    revision_extract: anth_sonnet

    # draft：写作主轴留 Opus，其余降 Sonnet
    paragraph_outline: anth_sonnet
    draft_polish: anth_sonnet

    # figure
    figure_plan: anth_sonnet
    figure_prompt: anth_haiku
```

### 档位 C · Frugal（订阅 + 中转）

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk             # Pro/Max 订阅
    model: claude-opus-4-7

  openai:                              # DeepSeek 走 OpenAI compatible
    mode: compatible
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY

  qwen_local:                          # 自部署 vLLM
    kind: openai
    mode: compatible
    model: qwen3-72b-instruct
    base_url: http://localhost:8000/v1
    api_key_env: VLLM_API_KEY          # vLLM 可不验，env 随便填

  anth_sonnet:                         # 红队保 Sonnet 拿 diversity
    kind: anthropic
    mode: api_key
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY

routing:
  default: anthropic                   # = claude_agent_sdk 订阅
  fallback: openai                     # 订阅挂了降级 DeepSeek
  overrides:
    # host orchestration：主对话直接做，零费
    summarize: host
    draft_compose: host
    draft_polish: host

    # ideate / review 主体降 DeepSeek
    ideate_brainstorm: openai
    relwork_cluster: openai
    review_persona_methodology: openai
    review_persona_statistics: openai
    review_persona_domain: openai

    # diversity 红队保 Sonnet
    idea_score_reviewer2: anth_sonnet
    review_persona_reviewer2: anthropic

    # panel 主体走自部署
    idea_score_methodology: qwen_local
    idea_score_novelty: qwen_local
    idea_score_impact: qwen_local

    # 抽取 / 分类走自部署
    claim_extract: qwen_local
    claim_judge: qwen_local
    revision_extract: qwen_local

    # 段落规划走自部署，写作主轴走订阅（节省大头）
    paragraph_outline: qwen_local

    # figure
    figure_plan: openai
    figure_prompt: qwen_local
```

---

## 何时升档 / 降档

按 `paic info` 解析的实际路由 + 实际跑下来的成本 / 质量调，不要按"感觉"调。

**从 B 升到 A**：
- 投稿前最后 1–2 周，draft 已经定稿，跑 `/paic-finalize` 多次都过——**这时候**把 `paragraph_write` 之外其他降级节点全提回 Opus，单稿差价 ~$15–40 但消除 review/critique 漏检风险
- 跨学科主题（例：CS+生物）领域 critique 覆盖度不够，把 `review_persona_domain` 单独提回 Opus 4.7

**从 B 降到 C**：
- 项目长（≥3 个月）日常迭代多 → 订阅省的钱超过 API 钱，切到 C
- 自部署有 vLLM / Ollama 跑 Qwen3 / Llama-4 → claim_judge / paragraph_outline 走本地 0 边际成本

**不该做的事**：
- ❌ 把 4 个 `idea_score_*` 全 override 到同一个非 default backend——只是把"同 backend"问题搬家，diversity 没变化、成本翻倍
- ❌ `default: host`——会让所有节点走 host，包括 review / ideate（不支持），graph 直接 crash
- ❌ `paragraph_write` 降到 Haiku / Qwen3-32B 以下——文笔退化人眼可辨，省的钱抵不过返工
- ❌ 给 `claim_judge` 升到 Opus——三分类任务无 lift，纯浪费 30× 价差

---

> 字段语义见 [configuration.md](configuration.md)；实战配方（命名 profile / fallback / host orchestration 全套 yaml / OpenAI 兼容代理 / 混合策略）见 [configuration-cookbook.md](configuration-cookbook.md)。改完跑 `uv run paic info` + `uv run paic doctor` 验证。
