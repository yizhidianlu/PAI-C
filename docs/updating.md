# PAI-C 本地更新

`git pull` 后让改动生效——按改动类型决定动作。

---

## 标准流程（覆盖 80% 场景）

```powershell
cd <PAI-C 仓库>
git pull
uv run python scripts/install_skills.py     # 同步 SKILL.md 到 ~/.claude/skills/
# 完全退出 Claude Code，重新打开
```

> **完全退出**指任务管理器中无 `Claude` 进程残留——非 `/clear`、非切换窗口。MCP server 是 Claude Code 启动时拉起的子进程，仅完全退出 + 重开会重新加载新代码。

---

## 按改动类型决定动作

| 改动文件 | 必要动作 |
|---|---|
| `pyproject.toml` / `uv.lock`（Python 依赖增减） | `uv sync` |
| `src/paic/**.py`（MCP 工具 / doctor / config / graphs / latex 等） | 完全重启 Claude Code |
| `skills/*/SKILL.md`（slash command 行为变更） | `uv run python scripts/install_skills.py` + 重启 |
| `src/paic/mcp_server/server.py` 新增 `@mcp.tool()` 或工具签名变更 | `uv run python scripts/register_mcp.py` + 重启 |
| `~/.paic/config.yaml`（用户修改 yaml） | 重启 Claude Code（config 在 server 启动时读取） |
| `docs/*.md` / `README.md` / `CLAUDE.md` | 无需动作 |

---

## 验证

重启后执行：

```powershell
uv run paic doctor       # 复查 credential / routing 行
uv run pytest -q         # 测试套件应全绿
```

`paic doctor` 输出含 ERR 时按 `fix:` 提示修复。`pytest` 失败通常是依赖未装齐——先 `uv sync`。

---

## 新机器首次部署

比标准流程多两步——装依赖 + 注册 MCP server：

```powershell
git clone https://github.com/yizhidianlu/PAI-C.git
cd PAI-C
uv sync
uv run python scripts/register_mcp.py
uv run python scripts/install_skills.py
# 配置 ~/.paic/config.yaml 选鉴权模式（详见 getting-started.md 第 4 节）
# 设置环境变量 ANTHROPIC_API_KEY / OPENAI_API_KEY / IEEE_API_KEY 等
uv run paic doctor
# 完全退出 → 重开 Claude Code
```

之后日常更新走上述「标准流程」。

---

## 常见误区

- **SKILL 未生效**：未执行 `install_skills.py`。SKILL 由 Claude Code 从 `~/.claude/skills/` 读取，不读仓库 `skills/`，必须主动同步。
- **重启后行为未变**：确认是「完全退出」而非 `/clear`。Windows 检查任务管理器中 `Claude` 进程；Mac/Linux 执行 `ps aux | grep claude`。
- **`uv sync` 报版本冲突**：通常 Python 低于 3.11。`uv python install 3.11` 后重试。
- **`paper-search-mcp` 装好但 `/paic-search` 未启用**：`~/.paic/config.yaml` 中 `providers.external_search.enabled` 仍为 `false`。改 `true` 后**重启** Claude Code。详见 [external-search.md](external-search.md)。
- **`paic doctor` 提示 `credential: ieee not set`**：`external_search.enabled=true` 且 preset 含 IEEE/ACM 但环境变量未设。按 `fix:` 行设置后**完全退出**重开。

---

## 进阶

- **回滚**：`git log --oneline` 找到目标 commit，`git checkout <sha> -- <file>` 单文件回退或 `git reset --hard <sha>` 整体回退（**会丢工作树修改**）。回退后仍需 `install_skills.py` + 重启。
- **多机同步**：机器 A push 后机器 B 走标准流程 pull。`~/.paic/config.yaml` 与 `~/.claude/skills/` 不进 git，每台机器各自维护。
- **查看更新进度**：`git log --oneline -10`（最近 10 commit）；`git log origin/main..HEAD`（本地领先远端）；`git log HEAD..origin/main`（远端领先本地）。
