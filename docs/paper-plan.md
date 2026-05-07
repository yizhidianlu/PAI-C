# Paper-quality 写作链路

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
  /paic-experiment       # 实验方案
  /paic-paper-plan       # paper-quality 入口：锁全局论点
  /paic-review           # 多轮评审，自动产 RevisionTask
  /paic-related-work-cluster   # 给 related work 分群
  /paic-draft fill --template <venue>
  /paic-draft compose --mode paragraph   # 用 paper_plan + claims + clusters
  /paic-figure plan      # 必须每个 contribution 有图 / 表 / 算法
  /paic-finalize         # 8 类检查（quality-gate.md）
```

`/paic-paper-plan` 是 paper-quality 阶段的入口，建议在 `/paic-experiment` 之后、`/paic-draft compose` 之前跑一次。

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

### 三个工具

```text
paic_paper_plan_create(project_dir, idea_id, experiment_id,
                       target_venue=None, audience=None, dry_run=False)
paic_paper_plan_update(project_dir, patch={"thesis": "...", ...})
paic_paper_plan_status(project_dir)
```

`create` 是首次生成；存在则报 `paper_plan_already_exists`，让用户选 `update` 或删文件重做。`update` 是浅合并 patch（list / dict 字段是整体替换，不是 append——要 append 先用 status 读旧 list 再传回）。

### 用法

```text
/paic-paper-plan
```

SKILL 流程：`status` 看是否已有 → 没有就 `create` → 有就渲染让用户决定 update / overwrite / 退出。

> Note: `section_plan[*].name` 必须是 canonical (`01_intro` / `02_related` / `03_method` / `04_experiments` / `05_discussion` / `06_conclusion`)，否则 compose 注入时找不到对应 entry，等于注入失败。

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

### 工具

| 工具 | 作用 |
|---|---|
| `paic_claims_init` | paper_plan.contributions 里每条种子化为 1 个 novelty 类 needs_evidence claim |
| `paic_claims_extract` | 给一段 LaTeX，LLM 抽里面的 claim-shape 句子（compose / polish 跑完自动调）|
| `paic_claims_validate` | 跨全部 claim 检查 cite_key 是否在 library、experiment_id 是否存在、强声明是否无支持 |
| `paic_claims_list` | 读全部 claim，可按 status 过滤 |

### 状态机

```text
needs_evidence ── 加 supporting_papers / supporting_experiments ──> supported
needs_evidence ── 用户加 \todo{} 标记 ────────────────────────────> todo
needs_evidence ── reviewer 否决 ──────────────────────────────────> rejected
```

`quality_gate` 只对**强声明**（novelty / comparative / numeric / result）的 needs_evidence 报警；factual claim 无支持是允许的。

---

## 3. Section-aware retrieval — BM25 + MMR

`compose_section` 旧版按 selected.yaml 顺序截前 40 篇。`paic_library_retrieve` 用 BM25 + MMR (lambda=0.7) 按 section 检索。**library > 40 篇时 compose 自动启用**；否则保持旧的全列表行为。

### 触发时机

| 场景 | retrieval 启用？ |
|---|---|
| compose 模式 `from_stub` / `from_scratch`，library ≤ 40 | ✗ 用插入顺序 |
| compose 任意模式，library > 40 | ✓ BM25 检索 top-40 |
| compose 模式 `paragraph` | ✓ 始终 retrieve top-20 给 outline |
| 手动调 `paic_library_retrieve` | ✓ |

### Query 模板

`build_query(section, paper_plan, idea, experiment)` 按 section 拼 query：

| Section | 拼料 |
|---|---|
| `01_intro` | section hint + thesis + idea title / one_liner / novelty_claim |
| `02_related` | hint + thesis + idea + 各 cluster label（如已聚类）|
| `03_method` / `04_experiments` | hint + experiment.proposed_method + datasets + baselines + metrics |
| `05_discussion` / `06_conclusion` | hint + thesis + 各 contribution 描述 |

### 手动调用

```text
paic_library_retrieve(project_dir, section="01_intro", k=12, mmr_lambda=0.7)
paic_library_retrieve(project_dir, query="motor imagery EEG channel selection", k=20)
```

返回 `{query, library_size, k, hits[]}`，每 hit 含 `cite_key` / `score` / `match_reason` / `snippet` / `title` / `year` / `authors`。

> Tip: `mmr_lambda=1.0` 是纯 BM25，`0.0` 是纯多样性。0.7 默认值在 50-200 篇 library 上是甜点。

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

`paic_related_work_cluster` 单 LLM call 把 library 分 3-5 群：

```yaml
clusters:
  - id: RW1
    label: "CSP family"
    axis: "method"          # method | dataset | task | limitation | contribution_type
    members: ["arxiv_a", "arxiv_b"]
    contrast_to_proposed: "Unlike CSP-family methods, we select channels per-subject."
```

落 `<project>/.paic/plans/related_work_clusters.yaml`。**`compose --mode paragraph --section 02_related` 会自动消费**：每 cluster 一段、cluster label 起头、contrast 作为段落 intent 驱动。

```text
paic_related_work_cluster(project_dir)
paic_related_work_status(project_dir)
```

> Note: 没跑 `paic_related_work_cluster` 时 02_related 段落会按 retrieval hits 直接 outline，效果不如有 cluster；不会报错。

---

## 6. Revision queue

`/paic-review` 跑完后调 `paic_revision_extract` 把 moderator + persona 评论转成 `RevisionTask` 队列。每条 task 落 `<project>/.paic/revisions/<round>_<id>.yaml`。

### Task 字段

```yaml
id: <ulid>
severity: blocker            # info | minor | major | blocker
status: open                 # open | in_progress | resolved | wontfix
target_kind: section         # section | claim | experiment_field | figure | table | global
target_ref: "01_intro"       # 具体引用，e.g. CL3 / baselines / F2
summary: "intro 缺第 3 条 contribution 的 motivation"
patch_hint: "在第 2 段后加一句…"
source_review_round: 1
source_persona: methodology
```

### 工具

| 工具 | 作用 |
|---|---|
| `paic_revision_extract` | review payload → 任务列表 |
| `paic_revision_list` | 按 status / severity / round 过滤 |
| `paic_revision_apply` | 标 in_progress（实际编辑由用户/SKILL 完成）|
| `paic_revision_resolve` | 标 resolved + 必填 resolution_summary |

### 多轮跟踪

```text
/paic-review               # round 1 → 8 个任务
# 用户 fix 5 个，调 paic_revision_resolve x5
/paic-review               # round 2 → 看 paic_revision_list(status="open") 还剩什么
                            # 新 round 产的 task source_review_round=2
```

---

## Troubleshooting

| 症状 | 原因 | 处理 |
|---|---|---|
| `/paic-draft compose` 跑完没生成 claims | claim_extract 是 best-effort，LLM 抽空 | 看 `claims.yaml` 是否存在，跑 `paic_claims_extract` 手动补；不影响 compose 成功 |
| retrieval 返回空 | library 全部 token 是 stopword / 太短 / query 语言与 library 错配 | 跑 `paic_library_retrieve(query="...")` 拿原始 query 试；或加 `paic_summarize` 补结构化 fields 让 BM25 命中 |
| paragraph 模式很慢 | N+2 次 LLM call，长 section 可能 ~1-3 min | 短 section 改回 `from_stub`；`mode="paragraph"` + `target_words=400` 折中 |
| `paper_plan_already_exists` | 重复跑 create | `paic_paper_plan_update` 增量改；或删 yaml 重跑 |
| `update` 后 list 字段被覆盖了 | patch 是浅合并、list 是整体替换 | 先 `status` 读全 list、append 新条、传完整 list 给 update |
| `revision_extract` 抽出 0 个 task | review payload 形状不对 | 确认传的是 `{moderator, critiques}` 或 `{synthesis, personas}` 形式 |
| compose 抛 `latex_validation_failed` | LLM 编了 cite_key 不在 library | 看 `cite_keys_missing_from_library`，要么 ingest，要么重 compose |

---

## 参考

- SKILL 源：`skills/paic-paper-plan/SKILL.md`
- 代码事实来源：`src/paic/schemas/paper_plan.py` / `claim.py` / `revision.py` / `related_work.py`
- 编排：`src/paic/latex/paragraph_compose.py` / `src/paic/library/retrieval.py` / `src/paic/library/clustering.py` / `src/paic/library/claims.py` / `src/paic/library/revisions.py`
- 提交前检查：[quality-gate.md](quality-gate.md)
