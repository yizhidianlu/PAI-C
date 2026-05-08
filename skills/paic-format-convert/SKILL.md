---
name: paic-format-convert
description: Convert manuscript files between Markdown / LaTeX / DOCX / PDF and re-render citations in 5 styles (APA 7 / Chicago / MLA / IEEE / Vancouver) via Pandoc + CSL. Pre-submission packaging — runs locally, no LLM call. Triggers on "convert to LaTeX", "convert to docx", "format paper", "pandoc", "导出 docx", "改 APA 引用".
allowed-tools: mcp__paic__paic_format_convert
---

# /paic-format-convert — Pandoc-driven document & citation conversion (ARS-fusion P0-3)

## When to use

- 投稿前要把 LaTeX draft 转 docx / pdf 给共同作者审
- 投稿要求换引文风格（APA 7 ↔ Chicago ↔ MLA ↔ IEEE ↔ Vancouver）
- 在两个 venue 之间复用同一份 manuscript

**前置依赖**：本机装好 `pandoc`。PDF 输出还需要 LaTeX 引擎（TeX Live / MikTeX / Tectonic）。

```
brew install pandoc       # macOS
apt-get install pandoc    # Ubuntu
choco install pandoc      # Windows (or download from pandoc.org)
```

## Flow

### Step 1 — 检查 pandoc

```
mcp__paic__paic_format_convert(
    project_dir=<cwd>,
    input_path="drafts/main.tex",
    output_path="drafts/main_preview.docx",
    target_format="docx",
)
```

无 pandoc 时返回 `{error: "pandoc_unavailable", hint: "..."}` — 把 hint 给用户照着装。

### Step 2 — 引文风格转换（可选）

```
mcp__paic__paic_format_convert(
    project_dir=<cwd>,
    input_path="drafts/main.tex",
    output_path="drafts/submission.docx",
    target_format="docx",
    citation_style="apa7",        # 可选: apa7 / chicago / mla9 / ieee / vancouver
    bibliography="drafts/refs.bib", # 可选；默认读 .paic/drafts/refs.bib
)
```

**CSL 文件**（v1 不 vendor）：用户从 https://github.com/citation-style-language/styles 下载对应 `.csl`，放到 `~/.paic/csl/`。返回里 `csl_path_used` 是 None 时 pandoc 用内置默认风格（不报错，但风格可能不是用户要的）。

CSL 别名表：
- `apa7` / `apa` → `apa.csl`
- `chicago` / `chicago-author-date` → `chicago-author-date.csl`
- `mla9` / `mla` → `modern-language-association.csl`
- `ieee` → `ieee.csl`
- `vancouver` → `vancouver.csl`

### Step 3 — 渲染响应

```
✅ 转换完成
   input:  drafts/main.tex
   output: drafts/submission.docx
   citation_style: apa7
   csl_path_used: ~/.paic/csl/apa.csl
   pandoc returncode: 0
```

或失败：

```
❌ pandoc returncode=1
   stderr: <最后 4KB>
```

把 stderr 给用户看，让用户决定下一步（typo / 缺包 / 转换不支持等）。

## 错误处理

- `error: project_not_initialized` → `/paic-init` 先建项目
- `error: pandoc_unavailable` → 装 pandoc 然后重启 Claude Code
- `error: input_not_found` → 检查 input_path 是否对（绝对路径或项目相对路径）
- `error: bibliography_not_found` → 检查 bib 文件路径，或 omit 让默认查 `.paic/drafts/refs.bib`
- `error: pandoc_failed` → 看 stderr，常见：LaTeX 包缺失（PDF 输出）、不支持的格式对、citeproc 报错（cite 在 bib 里没找到）

## Style

- 中文叙述。文件路径 / 风格别名 / 错误码保留英文。
- 第一次成功转换前给一句 cost transparency：「pandoc 本地跑不耗 token；PDF 输出需要 LaTeX 引擎，编译可能花 10-30s」。
- CSL 风格转换 = 重 render，不改 bib 内容；用户下次想换风格直接改 `citation_style` 重跑即可。

## 已知陷阱

- **CSL 没找到 silently fallback**：返回会带 `notes` 提示，但 pandoc 不报错。强烈建议用户第一次跑前先确认 CSL 在搜索路径里。
- **Markdown → PDF 走 pdflatex**：默认 pdflatex；中文字符渲染需要 xelatex 或 lualatex（CLAUDE.md "不做的事" 已声明 PAI-C 不支持中文 LaTeX 输出）。
- **DOCX → LaTeX**：pandoc 能转，但 DOCX 里的 native equation 只转一部分；公式多的论文建议从 LaTeX 起步而不是 DOCX 起步。
- **LaTeX 转 DOCX 后 cross-ref 丢失**：pandoc 把 `\ref{}` 转成 plain text，链接关系丢；DOCX 里要重做 cross-ref。
- **bib 里 cite 不全**：`\cite{xyz}` 但 `xyz` 不在 refs.bib → pandoc citeproc 报错。先跑 `/paic-finalize` 的 `undefined_cites_refs` 检查再来。
