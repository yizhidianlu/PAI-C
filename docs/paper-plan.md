# Paper-quality 写作链路

> **Feature** · `paper_plan` / `claims` / retrieval / paragraph compose / clusters / revisions —— v0.1 之上让生成出的论文保持全局一致的 paper-quality 工具链。

PAI-C 在 v0.1 之上加了一组 **paper-quality** 工具，把「散点式生成 → 一致性论文」的链路串起来：全局论点锁在 `paper_plan.yaml`，强声明留痕到 `claims.yaml`，章节按 BM25 检索证据、按段落级 outline 写、按 cluster 组织 related work，review 评论自动转 RevisionTask。

本文档覆盖**写作侧**所有新功能。提交前的一致性检查见 [quality-gate.md](quality-gate.md)。

---

## 端到端工作流

```text
/paic-init
  /paic-search           # 找文献
  /paic-ingest           # 入库 + 下载
  /paic-summarize        # 结构化摘要
  /paic-ideate           # 生成 idea
  /paic-experiment       # 实验方案（experiment-first 路径）
  /paic-paper-plan       # paper-quality 入口：锁全局论点
  /paic-review           # 多轮评审，自动产 RevisionTask
  /paic-related-work-cluster   # 给 related work 分群
  /paic-draft fill --template <venue>
  /paic-draft compose --mode paragraph   # 用 paper_plan + claims + clusters
  /paic-figure plan      # 必须每个 contribution 有图 / 表 / 算法
  /paic-finalize         # 8 类检查（quality-gate.md）
```

`/paic-paper-plan` 是 paper-quality 阶段的入口。**两种合法顺序**，都必须在 `/paic-draft compose` 之前完成：

- **Experiment-first**（默认；v0.1 文档示例）：`/paic-ideate` → `/paic-experiment` → `/paic-paper-plan`。一次生成含实验细节的完整 paper plan。
- **Thesis-first**（thesis-driven 写作的自然顺序）：`/paic-ideate` → `/paic-paper-plan`（**省略 `experiment_id`**，仅 idea 锁论点 + 贡献 + section 意图，方法 / 评估保持高层描述）→ `/paic-experiment`（按 paper_plan.contributions 设计实验）→ `paic_paper_plan_update(patch={"experiment_id": "<id>"})` 绑定，或删 `paper_plan.yaml` 重 create。

---

## 1. paper_plan.yaml — 全局论点

`/paic-paper-plan` 单 LLM call 从 idea + experiment + library 生成一份 `<project>/.paic/plans/paper_plan.yaml`，捕获**贡献清单 / 章节意图 / 术语 / 符号 / 图表槽位**。compose / claim ledger / quality gate 全部以它为 ground truth。

### 字段速查

| 字段 | 类型 | 说明 |
|---|---|---|
| `thesis` | str | 一句话核心主张 |
| `target_venue` | str / null | 目标会议刊物 |
| `audience` | str / null | 主要读者一句话描述 |
| `contributions[]` | list | 每条 `id` (C1/C2/...) + `title` + `description` |
| `section_plan[]` | list | 每章 `name` (`01_intro` / `02_related` ...) + `intent` + `supports_contributions` + `target_words` |
| `terminology` | dict | `术语 -> 简短定义`，compose 强制保持一致 |
| `symbols` | dict | `\theta -> 模型参数` |
| `figure_plan[]` | list | 预留 figure 槽位，phase 9 用 |
| `table_plan[]` | list | 预留 table 槽位 |
| `algorithm_plan[]` | list | 预留 algorithm 槽位 |
| `open_todos[]` | list | 未决待用户确认的事项 |

### 用法

```text
/paic-paper-plan
```

SKILL 流程：先看是否已有 plan → 没有就 create → 有就渲染让用户决定 update / overwrite / 退出。

**两种合法顺序**（都在 `/paic-draft compose` 之前完成）：

- **Experiment-first**（默认）：`/paic-ideate` → `/paic-experiment` → `/paic-paper-plan` 一次到位，含实验细节
- **Thesis-first**（thesis-driven 写作的自然顺序）：`/paic-ideate` → `/paic-paper-plan`（**省略 experiment_id**，仅 idea 锁论点 + 贡献，方法 / 评估保持高层描述）→ `/paic-experiment`（按 contributions 设计实验）→ update 绑 `experiment_id`，或删 plan 重 create

更新已有 plan 用 update 增量改（patch 浅合并；list / dict 是整体替换，要 append 需先读全列表）。

> Note: `section_plan[*].name` 必须是 canonical (`01_intro` / `02_related` / `03_method` / `04_experiments` / `05_discussion` / `06_conclusion`)，否则 compose 注入时找不到对应 entry。

---

## 2. claims.yaml — 强声明留痕

`<project>/.paic/plans/claims.yaml` 跟踪每条 claim 的**类型 / 状态 / 支持来源**。compose / polish 跑完会自动追加新发现的 claim；提交前 `quality_gate` 会检查是否有「needs_evidence 强声明」。

### Claim 字段

```yaml
- id: CL_01_intro_1_<ulid>
  text: "Our method beats CSP by 3.2 percentage points on BCI-IV-2a."
  type: comparative          # novelty | comparative | factual | numeric | methodological | result
  status: needs_evidence     # needs_evidence | supported | todo | rejected
  contribution_id: C2
  supporting_papers: ["arxiv_xxx"]
  supporting_experiments: ["exp_1"]
  required_citations: ["arxiv_xxx"]
  appears_in_sections: ["04_experiments"]
```

### Claim ledger 行为

- **种子化**：paper_plan.contributions 每条自动产 1 个 novelty 类 needs_evidence claim
- **抽取**：`/paic-draft compose` / `polish` 跑完自动从生成的 LaTeX 里 extract claim-shape 句子；可手动补抽
- **校验**：跨全部 claim 检查 cite_key 是否在 library、experiment_id 是否存在、强声明是否无支持
- **列表**：可按 status / type 过滤查看

### 状态机

```text
needs_evidence ── 加 supporting_papers / supporting_experiments ──> supported
needs_evidence ── 用户加 \todo{} 标记 ────────────────────────────> todo
needs_evidence ── reviewer 否决 ──────────────────────────────────> rejected
```

`quality_gate` 只对**强声明**（novelty / comparative / numeric / result）的 needs_evidence 报警；factual claim 无支持是允许的。

---

## 3. Section-aware retrieval — BM25 + MMR

旧版 compose 按 `selected.yaml` 顺序截前 40 篇；现在用 BM25 + MMR (lambda=0.7) 按 section 检索。**library > 40 篇时 compose 自动启用**；否则保持旧行为。

### 触发时机

| 场景 | retrieval 启用？ |
|---|---|
| compose 模式 `from_stub` / `from_scratch`，library ≤ 40 | ✗ 用插入顺序 |
| compose 任意模式，library > 40 | ✓ BM25 检索 top-40 |
| compose 模式 `paragraph` | ✓ 始终 retrieve top-20 给 outline |

### Query 拼接

按 section 自动拼 query：

| Section | 拼料 |
|---|---|
| `01_intro` | section hint + thesis + idea title / one_liner / novelty_claim |
| `02_related` | hint + thesis + idea + 各 cluster label（如已聚类）|
| `03_method` / `04_experiments` | hint + experiment.proposed_method + datasets + baselines + metrics |
| `05_discussion` / `06_conclusion` | hint + thesis + 各 contribution 描述 |

每 hit 含 `cite_key` / `score` / `match_reason` / `snippet` / `title` / `year` / `authors`。

> Tip: `mmr_lambda=1.0` 是纯 BM25，`0.0` 是纯多样性；0.7 默认值在 50–200 篇 library 上是甜点。

### Chunk-level grounding（v0.2）

paragraph 模式 compose 不只看论文标题 / 摘要，而是注入**实际段落**作为 LLM 的可引依据：

- `/paic-summarize` 之后会自动按 markdown heading 切分论文为 ~400 token 的 chunk，落在 `<project>/.paic/library/chunks/<cite_key>.json`。
- compose 在 paragraph 模式调 retrieval 时附带 `chunks_per_paper=3`：每个候选论文返回 BM25 排序后的前 3 个 chunk。outline 阶段看到 chunk 摘要选 cite_key，write 阶段看到完整 chunk 内容、必须**为每个 `\cite{KEY}` 写一句来自该论文 chunk 的 inline supporting paraphrase**（`paragraph_write.md` system prompt 里硬性约束）。
- 老项目（v0.1 之前没 chunks/）自动降级为 paper-level snippet —— 不阻断 compose，但 inline 引用的 grounding 较弱。建议运行一次 `paic_library_reindex_chunks` 一次性补齐：

```text
mcp__paic__paic_library_reindex_chunks(project_dir="<cwd>")
```

返回 `{library_count, indexed[], skipped[], total_chunks, chunks_dir}`。`skipped[]` 含未找到 markdown 的 cite_key，常见原因是 `library/pdfs/<cite_key>.md` 不存在 —— 通常表示该 paper 来自 arxiv MCP 而没用 `paic_library_attach_paper` 复制本地。

claim semantic judge（`paic_claims_validate(semantic=true)`) 同步升级：当 cite_key 有 chunks 时，会先按 claim 文本 BM25 选 top-3 chunks 作为 paper context 喂给 judge，比 summary 全文更精准。`Claim.supporting_chunks` 字段（默认 `[]`）允许 claim 显式锚定到特定 chunk_ids，judge 优先用那些 chunk 而非 BM25 自选。

---

## 4. Paragraph mode compose

```text
/paic-draft compose --mode paragraph
```

把 section 的生成拆成三步：

1. **outline** — 1 次 LLM call 产出 `ParagraphSpec` 列表（每段 `id` / `role` / `intent` / `claim_ids` / `cite_key_candidates` / `target_words`）。
2. **write_paragraph** — N 次 LLM call（每段一次），上下文带前面已写段落避免重复。
3. **coherence_polish** — 1 次 LLM call 平滑过渡、消除重复短语、保留所有 `\cite{}` 不动。

### 何时用 paragraph

| 场景 | 推荐模式 |
|---|---|
| 短 section（abstract / conclusion，< 300 字）| `from_stub` |
| 中 section（method 没复杂 outline）| `from_stub` |
| 长 section（intro / related / experiments，> 600 字）| `paragraph` |
| paper_plan + claims 都齐全的项目 | `paragraph`（全程留痕）|
| LLM 预算紧 | `from_stub`（1 次 call vs N+2 次）|

### Role 类型

`motivation` / `background` / `contrast` / `method` / `result` / `discussion` / `summary` / `transition`。outline 阶段会按 section 自动决定段落 role 序列。

---

## 5. Related-work clustering

单 LLM call 把 library 分 3–5 群，落 `<project>/.paic/plans/related_work_clusters.yaml`：

```yaml
clusters:
  - id: RW1
    label: "CSP family"
    axis: "method"          # method | dataset | task | limitation | contribution_type
    members: ["arxiv_a", "arxiv_b"]
    contrast_to_proposed: "Unlike CSP-family methods, we select channels per-subject."
```

`compose --mode paragraph --section 02_related` 会**自动消费**：每 cluster 一段、cluster label 起头、contrast 作为段落 intent 驱动。

> Note: 不聚类直接 compose `02_related` 也行——会按 retrieval hits 直接 outline，效果略差但不报错。

---

## 6. Revision queue

`/paic-review` 跑完后，moderator + persona 评论自动转成 `RevisionTask` 队列。每条 task 落 `<project>/.paic/revisions/<round>_<id>.yaml`。

### Task 字段

```yaml
id: <ulid>
severity: blocker            # info | minor | major | blocker
status: open                 # open | in_progress | resolved | wontfix
target_kind: section         # section | claim | experiment_field | figure | table | global
target_ref: "01_intro"       # 具体引用，如 CL3 / baselines / F2
summary: "intro 缺第 3 条 contribution 的 motivation"
patch_hint: "在第 2 段后加一句……"
source_review_round: 1
source_persona: methodology
```

### 多轮跟踪

```text
/paic-review               # round 1 → 8 个任务
# 用户 fix 5 个、标记 resolved（每条带 resolution_summary）
/paic-review               # round 2 → 看 status=open 还剩什么
                            # 新一轮的 task source_review_round=2
```

可按 `status` / `severity` / `round` 过滤查看；apply 标 in_progress（实际编辑由用户 / SKILL 完成）；resolve 标 resolved（必填 resolution_summary）。

---

## Troubleshooting

| 症状 | 原因 | 处理 |
|---|---|---|
| `/paic-draft compose` 跑完没生成 claims | claim 抽取是 best-effort，LLM 抽空 | 不影响 compose 成功；跑一次手动 extract 即可 |
| retrieval 返回空 | library 全部 token 是 stopword / 太短 / query 语言与 library 错配 | 用原始 query 串再试；或先 `/paic-summarize` 补结构化 fields 让 BM25 命中 |
| paragraph 模式很慢 | N+2 次 LLM call，长 section 可能 1–3 min | 短 section 改回 `from_stub`；`paragraph` + `--target-words 400` 折中 |
| `paper_plan_already_exists` | 重复跑 create | 走 update 增量改；或删 yaml 重跑 |
| update 后 list 字段被覆盖了 | patch 是浅合并、list / dict 是整体替换 | 先读全 list、append 新条、再传完整 list |
| revision 抽出 0 个 task | review payload 形状不对 | 确认 review 是从 `/paic-review` 正常跑完的 |
| compose 报 `latex_validation_failed` | LLM 编了 cite_key 不在 library | 看 `cite_keys_missing_from_library` —— ingest 缺失论文，或重 compose |

---

## 参考

- 提交前一致性检查：[quality-gate.md](quality-gate.md)
- 配置（路由 / host orchestration / 节流）：[configuration.md](configuration.md)
