---
name: paic-disclosure
description: Generate a venue-specific AI usage disclosure paragraph (ICLR / NeurIPS / Nature / Science / ACL / EMNLP + generic fallback). RAISE-framework fields embedded per venue policy. Pure template render — no LLM call. Triggers on "AI disclosure", "generate AI usage statement", "投稿合规声明", "AI 声明 [venue]".
allowed-tools: mcp__paic__paic_disclosure_generate
---

# /paic-disclosure — venue-specific AI usage disclosure (ARS-fusion P0-3)

## When to use

- 投稿前需要按目标 venue 的政策写 AI 使用声明
- 当前支持 venue：`iclr2026` / `neurips2026` / `nature` / `science` / `acl` / `emnlp` / `generic`（fallback）
- **不要** 用来生成内容真伪声明 / 利益冲突声明 / 数据可获得性声明 — 那些每个 venue 自己有模板，PAI-C 不接管

## Flow

### Step 1 — 收集 AI 工具清单

询问用户：

```
请列出本论文准备过程中用到的 AI 工具（每条含 4 字段）：
  · name      — 工具名（如 "Claude Code", "ChatGPT-4", "GitHub Copilot"）
  · stage     — 哪个阶段用（ideation / literature_review / drafting / analysis / revision / formatting）
  · extent    — 程度（minor / moderate / extensive）
  · purpose   — 具体用途（一句话）

示例：
  - {name: "Claude Code", stage: "drafting", extent: "extensive", purpose: "draft polish + claim extraction"}
  - {name: "ChatGPT-4", stage: "literature_review", extent: "minor", purpose: "summarize 3 papers"}
```

### Step 2 — 调用生成

```
mcp__paic__paic_disclosure_generate(
    project_dir=<cwd>,
    venue="iclr2026",                          # 必填
    tools=[
        {"name": "Claude Code", "stage": "drafting", "extent": "extensive", "purpose": "draft polish + claim extraction"},
        ...
    ],
    paper_title="Long-context attention for medical report generation",  # 可选
    author_responsibility_note=<可选 — 默认有，需要 reword 时才填>,
    raise_equity_note=<可选 — 涉及 equity 影响时填>,
    extra_lines=<可选 — 加 venue-specific 额外段>,
    output_format="markdown",                  # "markdown" | "latex"
    write_to="drafts/disclosure.md",           # 可选，自动写盘
)
```

### Step 3 — 渲染响应

```
📄 AI Disclosure (venue=iclr2026, format=markdown)

<rendered content>

📍 Placement: <placement_hint>
   ICLR 2026: 放在 abstract 末尾或独立 "AI Disclosure" section（references 前）

✏  template: src/paic/format/templates/disclosure/iclr2026.md.j2
💾 written to: drafts/disclosure.md   (如果 write_to 给了)
```

把 placement_hint 强调给用户 — 不同 venue 对位置要求差异很大（ICLR/NeurIPS 在 paper 主体；Nature 在 Methods；Science 在 Acknowledgments；ACL/EMNLP 走 responsible NLP checklist）。

### Step 4 — 用户校对 + 粘进论文

提醒用户：
1. 校对 tool inventory 是否漏了什么（特别是 literature_review / analysis 阶段容易忘）
2. RAISE equity 段适用吗（accessibility / bias 相关研究强烈建议填）
3. 把 content 字段粘到论文对应位置（按 placement_hint）

## 错误处理

- `error: project_not_initialized` → `/paic-init` 先建项目
- `error: tools_required` → 至少给 1 条 AI 工具记录
- `error: invalid_output_format` → 只接受 `"markdown"` 或 `"latex"`
- `warnings: ["Unknown venue ..."]` → 用 generic 模板生成；用户可以自己手动改

## Style

- 中文叙述。venue id / 字段名 / placement_hint 保留英文。
- 模板渲染是 pure，无 LLM 调用 — 无 cost transparency 需要预告。
- 用户改完 tool inventory 后让他重跑就行（cost 0）。

## 已知陷阱

- **不当 venue 兜底**：venue 不在支持列表 → 用 generic 模板 + warning。Generic 是保守模板，可能比 venue-specific 严格；建议用户照 venue 官方 author guide 微调。
- **RAISE 框架不全 cover**：模板埋了 Responsibility / Authenticity / Integrity / Stewardship，equity 是 opt-in。等价文献综述 / 偏见研究类强烈推荐填 `raise_equity_note`。
- **PAI-C 自身也是 AI 工具**：用户经常忘把 "Claude Code" / "PAI-C" 列进 tools。第一次帮忙列时主动问「你跑了 PAI-C，这算 'Claude Code in drafting + literature_review' 吗？」
- **不替代利益冲突声明**：disclosure 是 AI usage 专用，conflict-of-interest / data-availability / ethics 各自有模板，照 venue author guide 自己写。
- **写盘后改不回**：`write_to` 直接覆盖目标文件，建议先不传 `write_to`、看 content 满意了再二次调用 + `write_to`。
