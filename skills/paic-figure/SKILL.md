---
name: paic-figure
description: Plan + generate paper figures via an AI image model (gpt-image-1 / DALL-E). Plan-first workflow — analyze the polished draft, propose ≤4 raster-friendly figure slots, then generate / edit / vary one at a time. Use after /paic-draft polish when the prose is stable enough to plan figures around.
allowed-tools: mcp__paic__paic_figure_plan, mcp__paic__paic_figure_generate, mcp__paic__paic_figure_edit, mcp__paic__paic_figure_variant, mcp__paic__paic_figure_list
---

# /paic-figure — paper-figure pipeline (Phase 1, raster only)

## When to use

- After `/paic-draft polish` 跑完，prose 稳定下来后开始配图
- 仅适合 **AI 图像模型擅长的图**：teaser / concept / domain 示意。架构图（boxes + arrows）和定量结果图（curves / bars）**不要走这条**——前者用 TikZ，后者用 matplotlib + 真实数据。

## Flow（plan → generate → 选优）

### Step 1 — 计划

1. 先确认要不要把已 polish 过的某个 `.tex` 路径作为 plan 输入；没 polish 完整稿就直接读 idea + experiment。
2. Call `mcp__paic__paic_figure_plan(project_dir=<cwd>, draft_path=<可选 .tex 绝对路径>, max_figures=4)`.
   - 若返回 `error: plan_exists`，问用户：要不要 `overwrite=True` 重做计划？还是手动改 `.paic/figures/_plan.yaml`？
   - 返回的 `slots` 是 list[dict]，逐 slot 渲染：
     ```
     [<slot>] kind=<kind> | section=<section_hint>
       位置: <position_hint>
       内容: <scene_description>
       caption: <caption_hint>
       理由: <rationale>
       绑定 claims: <supporting_claims>（§quality phase 9）
       no_visual_reason: <reason 或 缺省>
     ```
   - **§quality phase 9 — coverage 警告**：检查返回的 `coverage_warnings`。**非空**时一行一条提示：
     ```
     ⚠ contribution 'C2' (Empirical study) 没有 figure / table / algorithm 绑定也没填 no_visual_reason — 建议加一个 slot 把 supporting_claims=['C2'] 加上，或在 paper_plan.yaml 的 figure_plan 里加一个 no_visual_reason 条目。
     ```
     用户可以选：(a) 让 plan_overwrite=True 重跑、(b) 手改 `_plan.yaml` 加 supporting_claims、(c) 在 paper_plan 里加 figure_plan entry with no_visual_reason、(d) 暂时忽略。
3. 让用户挑选要继续生成的 slot（可以全选，也可以删几条；删的话用户去改 `_plan.yaml` 或我们用 `overwrite=True` 重 plan）。

### Step 2 — 生成（每张图一次调用）

对每个 slot：

1. Call `mcp__paic__paic_figure_generate(project_dir=<cwd>, slot=<slot_name>)`.
   - 若返回 `error: images_disabled` → 提示用户在 `~/.paic/config.yaml` 里加 `providers.images` 块（enabled: true / model: gpt-image-1 / base_url: <relay> / api_key_env: <env>）然后**完全重启 Claude Code**。
   - 若返回 `error: image_backend_failed` → 把 `detail` 字段贴给用户（中转站具体错误），让用户决定换 model 还是换 base_url。
2. 渲染响应（中文）：
   ```
   [<slot>] 生成 v<n>
     文件: <png_path>
     LLM 翻译后的图像 prompt: <image_prompt>

   LaTeX 片段（粘贴到对应 section）：
   <latex_snippet>
   ```
3. 询问用户：满意 / 想 edit / 想看几个 variant？

### Step 3 — 改图

- **edit**（局部调整 / 风格微调）:
  ```
  mcp__paic__paic_figure_edit(project_dir=<cwd>, slot=<slot>, instruction="使颜色暗一些，加一些海洋元素")
  ```
  默认改最新版。返回新版 `v<n+1>_edit.png`，meta.yaml 自动记录 parent_version。

- **variant**（基于现有版生成 N 个备选）:
  ```
  mcp__paic__paic_figure_variant(project_dir=<cwd>, slot=<slot>, n=2)
  ```
  适用于「就给我换个角度」「试两个不同配色」。

每次都返回新的 `latex_snippet`，提醒用户：换图后只需把 `\includegraphics{figures/<slot>/<version>.png}` 里的 `<version>` 改一下即可。

### Step 4 — 收尾

- Call `mcp__paic__paic_figure_list(project_dir=<cwd>)` 显示当前所有 slot 的版本数 + 最新版本号。
- 提醒用户：把最终选定版本对应的 `latex_snippet` 粘贴到 `.tex` 里；**别忘了 `\usepackage{graphicx}`**（PAI-C 模板默认有）。

## 错误处理

- `error: project_not_initialized` → `/paic-init` 先建项目
- `error: plan_exists` → 已有 `_plan.yaml`，要 `overwrite=True` 重做或手编
- `error: images_disabled` → 在 `~/.paic/config.yaml` 配 `providers.images.enabled=true`，重启
- `error: slot_not_in_plan` → 调用前先 `paic_figure_plan`，或 `free_slot=True + description=...`
- `error: description_required` → `free_slot=True` 必须配 `description`
- `error: no_existing_version` → 该 slot 还没生成 v1，先 `figure_generate`
- `error: edit_not_supported` → 当前 model（如 dall-e-3）不支持 edit；切到 `gpt-image-1` 或 `dall-e-2`
- `error: image_backend_failed` → 中转站/API 错误，看 `detail`

## Style

- 中文叙述。LaTeX 片段、文件路径、prompt 原文保留英文。
- 把决定权留给用户——是否 edit / variant / 接受 / 重 plan，都问一句。
- 生成成本不便宜（gpt-image-1 在 mytoken.top 之类的中转站约 ¥0.5-2/张），**别擅自一次性生成 N 张**；按需生成、按需 edit。

## 已知陷阱

- **不要用来画架构图 / pipeline diagram**：AI 图像模型对盒子+箭头+文字标签的处理几乎全错（labels 乱码、对齐错、连线断），用 TikZ 才靠谱。Phase 2 会加 TikZ 工具，目前自己写。
- **不要用来画 result plot**：精度数字、曲线趋势、坐标轴 AI 都会瞎编，结果图必须从真实实验数据出 matplotlib。
- **caption 要自己 polish**：`caption_hint` 是 plan 阶段 LLM 给的草稿，论文 caption 风格请自己改（用 `\caption{...}` 直接覆盖）。
- **cite-key 不要写到 caption 里**：图片 caption 与 polish 节点是分开的；`paic_draft_polish` 不会扫已粘贴的 figure 块，所以 `\cite{}` 写错了不会被 `cite_keys_preserved` 检测到。
- **gpt-image-1 不支持原生 variations**：variant 走 edits 端点 + "alternative variation" prompt 模拟，质量略不如 dall-e-2 的原生 variations，但 gpt-image-1 的 base 质量高很多，整体仍占优。
- **多次 edit 会偏移**：edit 是基于上一版的，连续 edit 3-4 次画面可能彻底偏离原意。建议每个 slot 至多 edit 2 次；不满意就回去改 `scene_description` 重新 generate。
- **path 是 project-relative**：返回的 `latex_snippet` 用 `figures/<slot>/<version>.png`，要求 LaTeX build root 是项目根目录（PAI-C 默认 scaffold 满足）。如果你把 `.tex` 移到了别处编译，需要相应调整路径。
