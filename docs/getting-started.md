# PAI-C 入门

> **Setup** · 5 分钟从零到第一条流水线 —— 安装、注册 Claude Code、跑通 `init → search → ingest → summarize`。完整 9 步链路见 [workflow.md](workflow.md)。

---

## 1. 前置依赖

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/)（包管理）
- [Claude Code](https://claude.com/claude-code)（slash command + MCP 宿主）
- arxiv MCP（PAI-C 不重写 arXiv 检索 / 下载，依赖此 MCP）

> Windows 下示例命令使用 PowerShell。Bash / Zsh 用户将 `[Environment]::SetEnvironmentVariable(...)` 替换为 `export ...`。

---

## 2. 拉取代码 + 安装依赖

```bash
git clone https://github.com/yizhidianlu/PAI-C.git
cd PAI-C
uv sync
```

`uv sync` 安装 `anthropic` / `claude-agent-sdk` / `openai` / `langgraph` / `httpx` 等依赖。

---

## 3. 注册到 Claude Code

```bash
uv run python scripts/register_mcp.py     # 写入 ~/.claude.json + 创建 ~/.paic/ + seed config.yaml
uv run python scripts/install_skills.py   # 复制 11 个 SKILL.md 到 ~/.claude/skills/
```

`register_mcp.py` 行为：

- 在 `~/.claude.json` 的 `mcpServers` 添加 `paic` 条目
- 创建 `~/.paic/` 全局工作区
- 从 `docs/config.yaml.example` 复制到 `~/.paic/config.yaml`（**已存在则不覆盖**）

`install_skills.py` 复制 `skills/paic-*/SKILL.md` 到 `~/.claude/skills/`。

---

## 4. 配置鉴权

PAI-C 不在 yaml 中读取 API key——全部来自环境变量或本机已登录的 Claude Code。四种模式按可用资源选择：

### 4A — API Key 模式（默认）

适用：拥有 `ANTHROPIC_API_KEY`、按 token 付费。

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

无需修改 `~/.paic/config.yaml`，seed 出来的就是 `api_key` 模式。

### 4B — Subscription 模式（Pro/Max 订阅、零 API 费）

复用本机 `claude login` 的 OAuth token。PAI-C MCP 通过 `claude-agent-sdk` 让本机 `claude` CLI 走订阅配额。

前置验证：

```bash
claude --version           # 输出版本号
claude login               # 完成授权流程
claude --print "say hi"    # 返回 Claude 回复（验证 OAuth）
```

编辑 `~/.paic/config.yaml`：

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk        # ← 修改此行
    model: claude-opus-4-7
routing:
  default: anthropic
  fallback: anthropic.api_key     # 可选：SDK 鉴权失败时降级到 API key（需同时设置 ANTHROPIC_API_KEY）
```

### 4C — Subscription + Host Orchestration（推荐 Pro/Max 用户）

在 4B 基础上让 `summarize` 节点直接由 Claude Code 主对话执行，PAI-C 不发起任何 LLM 调用，绕开 `claude_agent_sdk` 在 MCP 子进程中的 `auth_failed` 风险。

```yaml
providers:
  anthropic:
    mode: claude_agent_sdk
    model: claude-opus-4-7
routing:
  default: anthropic              # review / ideate / experiment 走订阅
  overrides:
    summarize: host                # summarize 走主对话，零 LLM 调用
```

详见 [configuration.md → Host Orchestration](configuration.md#host-orchestration订阅复用零外部-llm-调用)。

### 4D — 第三方 OpenAI 兼容中转站

适用：mytoken.top / OpenRouter / Azure OpenAI / 自建代理 / 本地 Ollama / vLLM 等。

```powershell
[Environment]::SetEnvironmentVariable("MYTOKEN_API_KEY", "sk-...", "User")
```

```yaml
providers:
  openai:
    mode: compatible
    model: gpt-5.5                          # 中转站暴露的模型名
    api_key_env: MYTOKEN_API_KEY
    base_url: https://mytoken.top/v1        # 中转站 URL
routing:
  default: openai
```

详见 [configuration.md → 第三方中转站](configuration.md#第三方中转站如-mytokentop)。

### 4E — 多平台论文检索（可选 opt-in）

`arXiv + Semantic Scholar` 对生物医学 / 经济 / 工程方向覆盖不全。安装上游 [`paper-search-mcp`](https://github.com/) 接入 PubMed / bioRxiv / OpenAlex / Crossref / IEEE / SSRN 等 20+ 平台：

```powershell
# 1. 安装 paper-search-mcp（替换 <paper-search-mcp> 为实际克隆路径）
cd <paper-search-mcp>
uv sync

# 2. 注册到 Claude Code（在 PAI-C 仓库根目录运行）
cd <PAI-C_REPO>
uv run python scripts/register_paper_search_mcp.py
```

在 `~/.paic/config.yaml` 启用：

```yaml
providers:
  external_search:
    enabled: true
    default_preset: biomed                  # cs_ml / biomed / physics_math /
                                            # econ_social / engineering / interdisciplinary
```

`/paic-init` 询问研究领域并列出对应平台清单。完整教程见 [`external-search.md`](external-search.md)。

> 默认关闭。CS/ML 用户可跳过此步。

### 4F — 论文配图（可选 opt-in）

启用 `/paic-figure` 流程（Phase 1 raster 配图）：

```yaml
providers:
  images:
    enabled: true
    model: gpt-image-1                       # 推荐：generate + edit；dall-e-3 仅 generate
    api_key_env: MYTOKEN_API_KEY             # 或 OPENAI_API_KEY，按 base_url 选
    base_url: https://mytoken.top/v1         # 留空走官方 api.openai.com
    size: 1024x1024
    quality: high
```

详见 `/paic-figure` SKILL 与 [configuration.md → images](configuration.md#images-paic-figure)。

### 其他可选环境变量

```powershell
# Semantic Scholar API key（启用 0.95 req/s 认证档；未设置时按 0.33 req/s 匿名档限流）
[Environment]::SetEnvironmentVariable("SEMANTIC_SCHOLAR_API_KEY", "s2k-...", "User")

# OpenAI（路由部分节点到官方 OpenAI 时使用）
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")
```

---

## 5. 自检

```bash
uv run paic doctor
```

每行输出 `[OK]` / `[WARN]` / `[ERR]`。**存在 ERR 行时不要继续**——错误项附带 `fix:` 提示。检查项：API key、`anthropic` / `claude_agent_sdk` / `openai` SDK 安装、`claude` CLI、arxiv MCP 存储路径、Semantic Scholar 速率档、当前路由配置。

每行含义见 [troubleshooting.md](troubleshooting.md)。

---

## 6. 重启 Claude Code

**完全退出后重新打开**（非 `/clear`，非切换窗口）。MCP server 不会热加载 `~/.claude.json` 或 `~/.paic/config.yaml`。

重启后在 Claude Code 输入 `/mcp`，应看到 `paic` 条目。否则参考 [troubleshooting.md → MCP 列表里没有 paic](troubleshooting.md#mcp-列表里没有-paic)。

---

## 7. 跑通第一个项目

新建空目录作为论文项目：

```bash
mkdir ~/papers/long-context-medical && cd ~/papers/long-context-medical
```

在该目录启动 Claude Code，依次输入：

```text
/paic-init 方向：long-context attention 在医学影像报告生成；目标 NeurIPS 2027。
```

```text
/paic-search 近两年 long-context transformer 在长文本摘要 / 医学报告生成的代表性工作。
```

```text
ingest 第 1, 3, 7 篇。
```

```text
/paic-summarize all
```

完成后 `.paic/` 内含：

- `project.yaml` — 项目元信息
- `library/selected.yaml` — 入库的 3 篇论文
- `library/summaries/*.md` — 3 份结构化摘要

至此检索 → 入库 → 摘要三段贯通。继续推进 ideate / experiment / review / draft / figure，详见 [workflow.md](workflow.md)。

---

## 下一步

- 端到端工作流：[workflow.md](workflow.md)
- Paper-quality 写作链路（`/paic-paper-plan` 锁论点、claim ledger、paragraph compose、related-work 聚类、revision 队列）：[paper-plan.md](paper-plan.md)
- 提交前一致性检查（`/paic-finalize` 8 类）：[quality-gate.md](quality-gate.md)
- `~/.paic/config.yaml` 全字段：[configuration.md](configuration.md)
- `git pull` 后生效：[updating.md](updating.md)
- 故障排查：[troubleshooting.md](troubleshooting.md)
