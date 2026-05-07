# Zotero 同步（可选）

PAI-C 在 `/paic-ingest` 跑完后**可选**把这批论文（含 DOI 元数据 / PDF 附件 / collection 组织）同步到本地或云端 Zotero。机制是 SKILL 层调用独立的 [zotero-mcp-server](https://github.com/54yyyu/zotero-mcp)（**不在** PAI-C repo 内），不需要任何额外的 PAI-C 配置——只要 zotero-mcp 注册到 Claude Code，PAI-C 的 `/paic-ingest` 就会自动检测到并询问用户是否同步。

> **PAI-C 不替换 zotero-mcp**——zotero-mcp 是独立的、可被 ChatGPT / Cherry Studio / Cursor 等其他客户端共用的 MCP server；PAI-C 只是消费它的几个 tool（`zotero_get_collections` / `zotero_create_collection` / `zotero_add_by_doi` / `zotero_add_by_url` / `zotero_add_from_file`）。

## 适用场景

- 你已经在用 Zotero 管理文献库，想把 PAI-C ingest 的新论文一并归档
- 你的协作者用 Zotero group library 共享文献，希望 PAI-C 推过去后大家都能看到
- 你需要在 Zotero 里查看 PAI-C 抓到的 PDF（PAI-C 自身的 `library/pdfs/<seq>_<title>.pdf` 文件名按数字索引，Zotero 有更友好的 UI）

不适合：你只想要 PAI-C 内部 LaTeX 引用，从不打开 Zotero——可以直接跳过本文。

## 前置依赖

- **Zotero 桌面 app**（macOS / Windows / Linux 都行，**最低 Zotero 7**——本地 API 需要 Zotero 7+）
- **Python 3.10+**（zotero-mcp-server 要求）
- **Claude Code**（你已经在用了）

## Step 1：装 Zotero 桌面 app + 启用本地 API

1. 从 [zotero.org/download](https://www.zotero.org/download/) 下载 Zotero 7+，装上、登录 Zotero 账号（免费即可）。
2. 启动 Zotero。**保持 Zotero 在后台运行**——zotero-mcp 通过 `localhost:23119` 与之通信，Zotero 关了 sync 就会失败。
3. （首次）确认本地 API 已启用：Zotero 主菜单 **Edit → Settings → Advanced → Config Editor**，搜 `extensions.zotero.httpServer.enabled`，应为 `true`（默认就是，只在被改过时才需要修）。

## Step 2：装 zotero-mcp-server

推荐 `uv tool` 全局装一次，跨项目共用：

```powershell
uv tool install zotero-mcp-server
```

或用 `pip` / `pipx`（任选其一）：

```powershell
pip install zotero-mcp-server
# 或
pipx install zotero-mcp-server
```

验证：

```powershell
zotero-mcp --version
```

> 想要语义检索 / PDF 抽取 / Scite 引用智能等扩展能力，参考 zotero-mcp 自身 README 的 `[semantic]` / `[pdf]` / `[scite]` extras。PAI-C 的 ingest 同步不依赖任何 extra。

## Step 3：注册到 Claude Code

PAI-C 跑在 Claude Code（CLI）下，注册位置是 `~/.claude.json` 的 `mcpServers` 段。两种方式：

### 方式 A：用 `claude mcp add`（推荐）

```powershell
# Windows / macOS / Linux 通用
claude mcp add zotero zotero-mcp -e ZOTERO_LOCAL=true
```

这条命令会自动在 `~/.claude.json` 加上：

```json
{
  "mcpServers": {
    "zotero": {
      "command": "zotero-mcp",
      "env": { "ZOTERO_LOCAL": "true" }
    }
  }
}
```

### 方式 B：手动编辑 `~/.claude.json`

打开 `~/.claude.json`（Windows: `%USERPROFILE%\.claude.json`；macOS / Linux: `~/.claude.json`），找到 `mcpServers` 段（不存在就新建），加：

```json
"zotero": {
  "command": "zotero-mcp",
  "env": { "ZOTERO_LOCAL": "true" }
}
```

**完全退出 Claude Code 再重启**（关全部 Claude Code 窗口 / 终端再打开）——MCP server 注册不热加载。

## Step 4：选认证模式

| 模式 | 适用 | 配置 |
|---|---|---|
| **本地 API**（推荐） | 单机用户、个人 personal library | `ZOTERO_LOCAL=true`（Step 3 已设） |
| **Web API** | 多设备同步、Group library、不开 Zotero 桌面 app | `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID` |

### Web API 模式（仅当本地模式不适用时）

1. 去 [zotero.org/settings/keys](https://www.zotero.org/settings/keys) 申请一个 API key（免费），勾选 read+write 权限。
2. 在同一页面找到你的 `User ID`（个人库用这个）或 `Group ID`（group library 用这个）。
3. 改 `~/.claude.json` 的 zotero 段：

```json
"zotero": {
  "command": "zotero-mcp",
  "env": {
    "ZOTERO_LOCAL": "false",
    "ZOTERO_API_KEY": "your-api-key-here",
    "ZOTERO_LIBRARY_ID": "1234567",
    "ZOTERO_LIBRARY_TYPE": "user"
  }
}
```

`ZOTERO_LIBRARY_TYPE` 取值：`user`（个人库，默认）/ `group`（group library）。

完全重启 Claude Code。

## Step 5：验证 PAI-C 集成

进任意 PAI-C 项目跑一次 ingest：

```
/paic-search motor imagery EEG
ingest 1, 2, 3
```

ingest 跑完最后，**SKILL 应该会问一句**：

```
检测到 zotero-mcp。本批 3 篇要同步到 Zotero 吗？(y/n，默认 n)
默认 collection: paic-ingest-<YYYYMMDD>
```

回 `y` → SKILL 调 `zotero_create_collection` + 逐篇 `zotero_add_by_doi`/`add_by_url`/`add_from_file`，最后渲染同步报告：

```
同步成功 3/3 篇（collection: paic-ingest-20260506）
```

回 Zotero 桌面 app 看 collection 列表，应该多了一个 `paic-ingest-20260506`，里面就是这 3 篇论文。

## 同步语义

### 路由优先级

按论文元数据有什么，SKILL 走第一条命中的路径：

| 优先级 | 条件 | 调的 zotero tool | 行为 |
|---|---|---|---|
| 1 | `paper.doi` 非空 | `zotero_add_by_doi` | Zotero 自动从 CrossRef 抓元数据 + 串联 Unpaywall / arXiv / PMC OA 抓 PDF |
| 2 | `paper.arxiv_id` 非空 | `zotero_add_by_url(https://arxiv.org/abs/<id>)` | Zotero 自动抓 arXiv 元数据 + PDF |
| 3 | 本地 PDF 已落地（`.paic/library/pdfs/<seq>_<title>.pdf`） | `zotero_add_from_file` | 上传本地文件，让 Zotero 试着从 PDF 抽 DOI 补元数据 |
| 4 | 三种都不可行 | 跳过 | 计入 `zotero_skipped`（reason: `no_doi_no_arxiv_no_pdf`） |

### 自动加的 tags

- `paic`（永远加）
- `paper.tags` 里所有用户/SKILL 自加的（`/paic-ingest <ids> tags=[review, baseline]`）

### 重复检测

`zotero_add_by_doi` 上游会去重——同 DOI 重复同步**不会**建副本，会返已存在的 item key，PAI-C 视为成功。所以你可以放心多次跑同一批 ingest，Zotero 端不会爆炸。

### 失败重试

每篇任意一种 zotero 调用失败 → SKILL silently retry 一次（除明确的 4xx）；仍失败计入失败列表，**不打断后续篇**。所以即便某一篇网络抖了，整批 ingest 还是会跑完。

## 单向 push 边界

PAI-C → Zotero 是**单向**，不维护反向同步。这意味着：

- 在 Zotero 改 metadata / 加 note / 移 collection / 加新 PDF 附件 → **不影响** PAI-C
- PAI-C 重 ingest 同一篇论文也**不会**覆盖你在 Zotero 端的人工注释（`zotero_add_by_doi` 命中已存在 item 时只合并标签 / collection，不动现有字段）

如果你需要 Zotero 端改动反过来同步进 PAI-C 的 `selected.yaml` —— 暂不支持。可以让我加，提个需求。

## 故障排除

| 症状 | 原因 | 处理 |
|---|---|---|
| `/paic-ingest` 跑完没问 Zotero | zotero-mcp 没注册 / 注册了但 Claude Code 没重启 | 跑 `claude mcp list` 看 zotero 是否在；重启 Claude Code |
| zotero 调用报 `connection refused` | Zotero 桌面 app 没开 / 本地 API 被禁 | 启动 Zotero；查 `extensions.zotero.httpServer.enabled` |
| `ZOTERO_LIBRARY_ID not set` | 选了 Web API 但 env 缺一个字段 | 检查 `~/.claude.json` zotero 段四个 env 都有；重启 |
| collection 一直建不上 | 同名 collection race / 权限 | 改名重试；group library 检查 API key 有 read+write 权限 |
| 同一篇被建了副本 | 走了 `zotero_add_from_file`（无 DOI 路径） + Zotero 没抽到 DOI | 对该篇手动在 Zotero 里 merge duplicates |
| SKILL 报 `zotero_skipped: no_doi_no_arxiv_no_pdf` | 论文 metadata 里 doi / arxiv_id 都为空且本地 PDF 没下到 | 用浏览器去原网站手取 PDF，attach 到 PAI-C 后再 sync；或直接在 Zotero 端手动加 |

## 进阶

- **改 collection 命名规则**：当前默认 `paic-ingest-<YYYYMMDD>`，SKILL 询问时可以改名或留空（不入 collection）。如果想改默认值——改 `skills/paic-ingest/SKILL.md` 里 step 5.1 的提示模板。
- **多个 Zotero library**（个人 + 多个 group）：当前 zotero-mcp 一次只连一个，由 `ZOTERO_LIBRARY_ID` 决定。要切换 → 改 env 重启。
- **Zotero 端的反向 metadata 抓取**（在 Zotero 改 abstract / venue 后能不能拉回 PAI-C `selected.yaml`）：暂未支持，提需求可加。
