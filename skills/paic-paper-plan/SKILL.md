---
name: paic-paper-plan
description: Generate or revise the project's global paper plan (`.paic/plans/paper_plan.yaml`). The plan locks in the central thesis, contributions, section intent, terminology, symbols, and reserved figure / table / algorithm slots so downstream compose / claim / quality-gate stages stay globally coherent. Use when the user says "起 paper plan" / "draft the paper plan" / "lock in the contributions" / "改 thesis" / before running `/paic-draft compose`.
allowed-tools: mcp__paic__paic_workspace_status, mcp__paic__paic_paper_plan_status, mcp__paic__paic_paper_plan_create, mcp__paic__paic_paper_plan_update
---

# /paic-paper-plan — global paper plan

## When to use

- User says: "起 paper plan" / "draft the paper plan" / "把 contribution 列表锁一下" / "before I compose intro 我先想清楚 paper 整体"
- 两种合法顺序，都在 `/paic-draft compose` 之前完成：
  - **Experiment-first**（v0.1 默认）：`/paic-ideate` → `/paic-experiment` → `/paic-paper-plan`，paper_plan 一次到位、含实验细节。
  - **Thesis-first**（thesis-driven 写作的自然顺序）：`/paic-ideate` → `/paic-paper-plan`（仅 idea，先锁论点 + 贡献 + section 意图）→ `/paic-experiment`（按贡献设计实验）→ 用 update 绑 `experiment_id` 或删文件重 create。
- 用户想改 thesis / contribution / terminology — 用 `update` 走小步修订，不要重新 create。

## What you do

1. **看当前 plan 状态**（永远先做）：
   ```
   mcp__paic__paic_paper_plan_status(project_dir=<cwd>)
   ```
   - `exists=false` → 进 step 2（create）
   - `exists=true` → 把 plan 渲染给用户看（thesis / contributions / section_plan 三段就够），再问 ① 微调（update） ② 推倒重来（删文件 + create） ③ 不动直接退出。

2. **Create**（首次或重建）：

   2a. **找 idea_id**（必填）+ **判断要不要带 experiment_id**（可选）：
   - **idea_id**：
     - 用户给了就用。
     - 没给：调 `paic_workspace_status`，列出最近的 idea 让用户选。
     - 一个 idea 都没有：提示「先跑 `/paic-ideate` 拿到 idea_id 再来 paper plan」。
   - **experiment_id**（可选）：
     - 用户已经显式给了 → 直接用（experiment-first 路径）。
     - 用户**没给**：从 `paic_workspace_status` 看这个 idea 是否已经有对应 experiment。有 → 询问「检测到该 idea 有 experiment `<exp_id>`，要带上吗？带 = experiment-first（一次锁完整 plan）；不带 = thesis-first（plan 仅 idea，跑完 /paic-experiment 后再 update 绑定）。(y/n，默认 y)」。没有 → 直接走 thesis-first（不带 experiment_id），告诉用户「当前先按 idea 锁论点 + 贡献，跑完 `/paic-experiment` 后 `paic_paper_plan_update(patch={'experiment_id': '<exp_id>'})` 绑定，或删 plan 重 create」。
   - **不要**主动催用户先去跑 `/paic-experiment`——thesis-first 是合法顺序。

   2b. **可选输入**：`target_venue`（"NeurIPS 2026" / "ICLR" / null）+ `audience`（一句话；没有就 null）。用户没主动说就不要硬问。

   2c. **调用**：
   ```
   mcp__paic__paic_paper_plan_create(
     project_dir=<cwd>,
     idea_id="<id>",
     experiment_id=<id 或 None>,   # None / 省略 = thesis-first；method/eval 高层描述
     target_venue=<str|null>,
     audience=<str|null>,
     dry_run=false,
   )
   ```

   2d. **错误处理**：
   - `paper_plan_already_exists` → 跳到 step 3（update）或问用户要不要先删 yaml 再重做
   - `idea_not_found` → 让用户 `/paic-status` 找正确 id
   - `experiment_not_found` → **只有用户显式传了不存在的 experiment_id 才会触发**；让用户 `/paic-status` 校对 id，或省略 experiment_id 走 thesis-first
   - `llm_unavailable` → 透传错误信息

3. **Update**（用户已经想改某些字段）：
   - 用户的话精确映射到 plan 字段。常见映射：
     - "thesis 改成 X" → `patch={"thesis": "X"}`
     - "contributions 加一条 / 删一条 / 调顺序" → 把全部 contributions 列表传进去（patch 是浅合并，list 是替换不是 append）
     - "把 method section target_words 改 1500" → 全部 section_plan 列表，找到对应 entry 改完再传
     - "terminology 加 'GAN: generative adversarial network'" → 把整个 terminology dict 传（merge 后）
   - 调用：
     ```
     mcp__paic__paic_paper_plan_update(project_dir=<cwd>, patch=<dict>)
     ```
   - 拿 `changed_keys` 一行报告改了什么。

4. **渲染产出**：

   - **Create 后**：
     ```
     ✓ paper_plan.yaml 已生成（路径 <path>）
     - thesis: <一句>
     - 贡献 N 条:
         [C1] <title>: <description>
         [C2] ...
     - section_plan: 6 段（intro 700w / related 800w / method 1200w / experiments 1000w / discussion 500w / conclusion 250w）
     - terminology: M 个核心术语
     - figure_plan: K 个 / table_plan: L 个 / algorithm_plan: P 个
     - open_todos: Q 项需用户确认
     ```
     接着列 open_todos（每条一行），让用户依次确认或忽略。

   - **Update 后**：
     ```
     ✓ paper_plan.yaml 已更新
     变更字段: [thesis, contributions]
     ```

   - 最后建议：「下一步：`/paic-draft compose <section>` 时 paper_plan 会自动注入 prompt」。

## Style

- 中文叙述。plan 字段名（`thesis` / `contributions` / `section_plan` / `terminology` / `symbols` / `figure_plan` / `table_plan` / `algorithm_plan` / `open_todos`）保留英文。
- contribution id 用 `C1` `C2` ...；figure / table / algorithm 用 `F1` `T1` `A1` ...。
- 不要预先评判 plan 好坏——把判断留给用户；但用户问"这 plan 看起来咋样"时可以指出明显问题（如 contribution 没绑任何 section、figure 数 vs contribution 数严重不匹配）。
- **rolling update 而非 create-from-scratch**：用户已经有 plan 时绝不要再调 create——只 update。

## 已知陷阱

- **patch 是浅合并、list/dict 是整体替换**：`patch={"contributions": [{"id": "C5", ...}]}` 会把现有 contributions **整个列表替换**为一项。要 append → 先读旧列表（`paper_plan_status`） + append 新条 + 整体传回。
- **paper_plan 与 idea card 不绑定 1:1**：用户改 idea 后再调 `paper_plan_create` 会被 `paper_plan_already_exists` 拒绝。需用户显式确认要不要重生（建议保留旧文件 .bak.<UTC>.yaml 后再删）；或者只走 update 同步 idea_id 字段。
- **section_plan 的 name 必须 canonical**：`01_intro` / `02_related` / `03_method` / `04_experiments` / `05_discussion` / `06_conclusion` —— 否则 compose 时 plan 注入会找不到对应 section_plan entry，等于注入失败。
- **terminology 大小写敏感**：`"GAN"` 和 `"gan"` 是两条记录。引导用户用一致大小写（推荐与 LaTeX 中实际写法一致）。
