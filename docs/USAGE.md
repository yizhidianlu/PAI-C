# PAI-C 使用文档

<p align="center">
  <img src="paic-workflow.png" alt="PAI-C workflow" width="900">
</p>

> Slash command 使用连字符：`/paic-search` ✓ ；`/paic:search` ✗ 。

---

## 文档导航

| 文档 | 适用场景 |
|---|---|
| [getting-started.md](getting-started.md) | 首次安装：依赖、注册 Claude Code、鉴权模式选择、跑通 init / search / ingest / summarize |
| [workflow.md](workflow.md) | 端到端工作流：每个 `/paic-*` 的产出位置、典型耗时、跨会话恢复 |
| [paper-plan.md](paper-plan.md) | Paper-quality 写作链路：paper_plan / claims / retrieval / paragraph compose / clusters / revisions |
| [quality-gate.md](quality-gate.md) | `/paic-finalize` 8 类提交前检查、override 语义、决策树 |
| [updating.md](updating.md) | `git pull` 后让改动生效：按改动类型决定 `install_skills` / `register_mcp` / 重启顺序 |
| [configuration.md](configuration.md) | `~/.paic/config.yaml` 全字段：4 个 LLM backend、命名 profile、host orchestration、Semantic Scholar、arxiv 多根、节流、多平台检索、图像 |
| [external-search.md](external-search.md) | 多平台论文检索（opt-in）：`paper-search-mcp` 接入 PubMed / bioRxiv / OpenAlex / IEEE 等 20+ 平台 |
| [custom-templates.md](custom-templates.md) | 自定义 LaTeX 模板：项目本地放 ICLR / Nature / TPAMI / 校刊等 venue 模板，scaffold 派生 + 静态 `.sty` / `.cls` 自动拷贝 |
| [zotero-sync.md](zotero-sync.md) | Zotero 集成（opt-in）：`/paic-ingest` 跑完后可选把论文同步到 Zotero（依赖独立 zotero-mcp，免 API key） |
| [overleaf-sync.md](overleaf-sync.md) | Overleaf 双向同步（opt-in）：`/paic-draft` 跑完后可选基于 Dropbox 同步到 Overleaf project（免 API key、免 Premium） |
| [troubleshooting.md](troubleshooting.md) | `paic doctor` 逐行解读、运行时错误目录、`/paic-finalize` 失败排查 |
| [config.yaml.example](config.yaml.example) | `~/.paic/config.yaml` 模板，含每段注释 |

> **四种鉴权模式**：(A) API key（默认）、(B) Pro/Max 订阅 `claude_agent_sdk`、(C) 订阅 + host orchestration、(D) 第三方 OpenAI 兼容中转站。详见 [getting-started.md § 4](getting-started.md) 与 [configuration.md](configuration.md)。

---

## TL;DR — 5 行装完

```bash
git clone https://github.com/yizhidianlu/PAI-C.git && cd PAI-C
uv sync
uv run python scripts/register_mcp.py        # 创建 ~/.paic/ 并 seed config.yaml
uv run python scripts/install_skills.py
# 鉴权选一：
#   (A) API key:      [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
#   (B/C) 订阅:       claude login；编辑 ~/.paic/config.yaml 设 providers.anthropic.mode=claude_agent_sdk；
#                     C 模式额外加 routing.overrides.summarize: host
#   (D) 中转站:       配置 providers.openai.mode=compatible + base_url；routing.default=openai
uv run paic doctor   # 全绿后完全退出 + 重启 Claude Code
```

完整说明见 [getting-started.md](getting-started.md) 第 4 节。

---

## TL;DR — 端到端跑完

进入论文项目目录，启动 Claude Code：

```text
/paic-init <方向描述>
/paic-search <检索词>
ingest 第 X, Y, Z 篇。
/paic-summarize all
/paic-ideate focus 在 <聚焦点>
/paic-experiment <idea_id>，约束：<硬件 + 时间>
/paic-paper-plan                          # paper-quality：锁全局论点
/paic-review 跑 2 轮 4-persona 评审       # 评论自动转 RevisionTask
/paic-draft fill 用 NeurIPS 模板
/paic-draft compose --mode paragraph      # 长 section 用 paragraph 模式
/paic-figure plan
/paic-finalize                            # 提交前 8 类一致性检查
```

每步细节、产出位置、跨会话恢复见 [workflow.md](workflow.md)。Paper-quality 链路（paper-plan → claims → retrieval → paragraph compose → clusters → revisions）见 [paper-plan.md](paper-plan.md)；提交前 finalize 8 类检查见 [quality-gate.md](quality-gate.md)。

---

## 命令速查

| 命令 | 用途 |
|---|---|
| `/paic-init` | 在当前目录建项目 |
| `/paic-search` | 多源检索 + 去重（arXiv + Semantic Scholar；opt-in `paper-search-mcp` 后扩到 20+ 平台，见 [external-search.md](external-search.md)） |
| `/paic-ingest` | 选定论文入库（自动触发 PDF 下载）；跑完可选同步到 Zotero，见 [zotero-sync.md](zotero-sync.md) |
| `/paic-summarize` | 结构化摘要 |
| `/paic-ideate` | 生成 idea（含用户筛选 checkpoint） |
| `/paic-experiment` | 设计实验方案 |
| `/paic-paper-plan` | 全局论文计划（thesis / contributions / section_plan / terminology），见 [paper-plan.md](paper-plan.md) |
| `/paic-review` | 4-persona 多轮评审（含 rebuttal checkpoint）；评论自动转 RevisionTask 队列，见 [paper-plan.md § 6](paper-plan.md) |
| `/paic-draft` | LaTeX 写作三阶：`fill` / `polish` / `compose`（可选 `--mode paragraph`）。内置模板见 [custom-templates.md](custom-templates.md)；可选 Overleaf 同步见 [overleaf-sync.md](overleaf-sync.md) |
| `/paic-figure` | 论文配图（raster，opt-in）：`plan` 提议 ≤4 张图位置；`generate` / `edit` / `variant` 逐张生成。仅适合 teaser / concept / domain；架构图用 TikZ、结果图用 matplotlib |
| `/paic-finalize` | 提交前 8 类 paper-level 检查，见 [quality-gate.md](quality-gate.md) |
| `/paic-resume` | 列出 / 继续未完成的 LangGraph run |
| `/paic-status` | 项目全景 |

---

## 开发者 CLI

| 命令 | 用途 |
|---|---|
| `uv run paic doctor` | 启动期诊断（API key / SDK / 路径 / 路由 / S2） |
| `uv run paic doctor --probe` | 上述检查 + 实活 `claude_agent_sdk` 握手（1-3s，约 10 token） |
| `uv run paic sdk-probe` | 直接探针 `claude_agent_sdk`，返回原始 SDK / CLI 错误 |
| `uv run paic info` | 显示当前生效的 config + 路由解析 |
| `uv run paic serve` | 手动启动 MCP server（调试用） |
| `uv run pytest` | 执行测试套件 |
