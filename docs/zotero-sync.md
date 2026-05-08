# Zotero 同步

> **Optional** · `/paic-ingest` 跑完后同步本批论文到 Zotero —— 依赖独立 [`zotero-mcp`](https://github.com/54yyyu/zotero-mcp)。**纯 Web API 模式**（`ZOTERO_LOCAL=false`）：读写都走 `api.zotero.org`，**必须配 Zotero API key + 真实 user ID**（免费申请）。Zotero 桌面 app 不需要打开（虽然开着帮你做 Zotero 服务器同步）。

PAI-C 在 `/paic-ingest` 跑完后**可选**把这批论文（含 DOI 元数据 / PDF 附件 / collection 组织）同步到 Zotero。机制是 SKILL 层调用独立的 zotero-mcp-server（**不在** PAI-C repo 内）——只要 zotero-mcp 注册到 Claude Code 且配齐 web mode env，PAI-C 的 `/paic-ingest` 就会自动检测到并询问用户是否同步。

> **⚠️ 不要用 hybrid 模式（ZOTERO_LOCAL=true + 真实 user ID）**：zotero-mcp v0.3.0 的 hybrid 设计是「read=local, write=web」，但**读客户端会把真实 user ID 拼进本地 URL** `http://localhost:23119/api/users/<id>/...`，触发 Zotero 本地 API 的硬约束 `Only data for the logged-in user is available locally -- use userID 0` 而 400 拒绝。结果是连 list collections 这种读操作都崩，更别说 ingest 同步。**唯一稳的配法是 `ZOTERO_LOCAL=false` 走纯 web**（详见 [Step 4](#step-4注册到-claude-code)）。
>
> **⚠️ 仅 `ZOTERO_LOCAL=true` + 不配 API key 也不行**：zotero-mcp 在 local-only 模式下**拒绝所有写操作**（`Cannot perform write operations in local-only mode`），PAI-C ingest 同步路径的 priority 1/2/3 全是写。

> **「Zotero 桌面 app 是不是不用开了？」** 严格说不必，但建议开着——这样 web 端写入后桌面 app 自动同步下来，你能在桌面 app 里立刻看到新 collection；关着的话，下次启动 Zotero 时再同步。

> **PAI-C 不替换 zotero-mcp**——zotero-mcp 是独立的、可被 ChatGPT / Cherry Studio / Cursor 等其他客户端共用的 MCP server；PAI-C 只是消费它的几个 tool（`zotero_get_collections` / `zotero_create_collection` / `zotero_add_by_doi` / `zotero_add_by_url` / `zotero_add_from_file`）。

## 适用场景

- 你已经在用 Zotero 管理文献库，想把 PAI-C ingest 的新论文一并归档
- 你的协作者用 Zotero group library 共享文献，希望 PAI-C 推过去后大家都能看到
- 你需要在 Zotero 里查看 PAI-C 抓到的 PDF（PAI-C 自身的 `library/pdfs/<seq>_<title>.pdf` 文件名按数字索引，Zotero 有更友好的 UI）

不适合：你只想要 PAI-C 内部 LaTeX 引用，从不打开 Zotero——可以直接跳过本文。

## 前置依赖

- **Zotero 账号**（[zotero.org](https://www.zotero.org/) 免费注册）—— Web API 写权限的源头
- **Zotero 桌面 app**（macOS / Windows / Linux 都行，**最低 Zotero 7**——本地 API 需要 Zotero 7+）
- **Python 3.10+**（zotero-mcp-server 要求）
- **zotero-mcp-server ≥ 0.1.5**（PAI-C 的本地 PDF 兜底路径需要 `zotero_add_from_file`，0.1.5 引入；当前推荐 0.3.0+）
- **Claude Code**（你已经在用了）

## Step 1：装 Zotero 桌面 app（建议但非必须）

1. 从 [zotero.org/download](https://www.zotero.org/download/) 下载 Zotero 7+，装上、登录 Zotero 账号（免费即可）。
2. 启动 Zotero——**纯 Web 模式下严格说不必开桌面 app**（PAI-C 通过 web API 直接写 Zotero 服务器），但**建议保持开着**：web 写入后 Zotero 服务器会通知桌面 app sync 下来，你能在桌面 UI 立刻看到新 collection 与论文；关着的话需要等下次启动 Zotero 才同步。
3. （可选）如果你想保留 zotero-mcp 的 local API 读路径给**其他客户端**（不是 PAI-C），首次确认 Zotero 主菜单 **Edit → Settings → Advanced → Config Editor**，搜 `extensions.zotero.httpServer.enabled` 为 `true`（默认就是）。同样在 Settings → Advanced 勾上 **Allow other applications on this computer to communicate with Zotero**。PAI-C 走 web 不依赖这两项。

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
zotero-mcp version
zotero-mcp setup-info     # 看安装路径 + 当前 env，便于排错
```

> **注意 zotero-mcp 是子命令风格**（`version` / `serve` / `setup` / `setup-info`），不是 GNU 风格 flag。`zotero-mcp --version` 会报 `unrecognized arguments`，去掉 `--` 即可。

> `zotero-mcp setup-info` 输出的 "Claude Desktop config at ..." 与本指南无关——那是给 Claude Desktop 用户的；本仓库的 PAI-C 跑在 **Claude Code** 上，配置位置完全不同（见 Step 3）。

> 想要语义检索 / PDF 抽取 / Scite 引用智能等扩展能力，参考 zotero-mcp 自身 README 的 `[semantic]` / `[pdf]` / `[scite]` extras。PAI-C 的 ingest 同步不依赖任何 extra。`zotero-mcp setup` 子命令只用于 Claude Desktop + ChromaDB 语义索引，**Claude Code 用户无需跑**。

## Step 3：申请 Zotero API key + 找 user ID

> **为什么是 mandatory**：zotero-mcp 在仅 `ZOTERO_LOCAL=true` 下**只支持读**，PAI-C ingest 同步流程的写入路径（priority 1/2/3）全部依赖 Zotero Web API，必须配 `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID`。

1. 登录 [zotero.org/settings/keys](https://www.zotero.org/settings/keys)（用你的 Zotero 账号）。
2. 点 **Create new private key**：
   - **Key Description**：任意（如 `paic`），方便后续在密钥列表识别 / 撤销
   - **Personal Library** 区勾上 `Allow library access` + `Allow write access`（写权限是关键）
   - 如果你想同步到 group library，把对应的 group 也加 `Read/Write`
   - 提交后页面**只显示一次**完整 key（形如 `DXmn3iZsTkQyYk9HHqTIdJdn`），立刻复制好
3. 在 [zotero.org/settings/keys](https://www.zotero.org/settings/keys) 顶部找到 **Your userID for use in API calls**（一串 7-8 位纯数字，如 `20490436`）。group library 用 `https://www.zotero.org/groups/<group-id>` 那串数字。

收好这两样，进 Step 4。

## Step 4：注册到 Claude Code

> **预检 30 秒（key 是否对）**：
>
> ```powershell
> curl -H "Zotero-API-Key: <你的-key>" "https://api.zotero.org/keys/current"
> ```
>
> 应返回 JSON 包含 `userID`、`access.user.write: true`。否则 key 错或 write 权限没勾——回 [Step 3](#step-3申请-zotero-api-key--找-user-id) 重申请。

PAI-C 跑在 Claude Code（CLI）下，注册位置是 `~/.claude.json` 的 `mcpServers` 段。两种方式都需要把 Step 3 拿到的两个值塞进 env，**关键：`ZOTERO_LOCAL=false`**：

### 方式 A：用 `claude mcp add`（推荐）

```powershell
# Windows / macOS / Linux 通用
claude mcp add zotero zotero-mcp `
  -e ZOTERO_LOCAL=false `
  -e ZOTERO_API_KEY=DXmn3iZsTkQyYk9HHqTIdJdn `
  -e ZOTERO_LIBRARY_ID=20490436 `
  -e ZOTERO_LIBRARY_TYPE=user
```

> 把上面的 key 与 ID 换成你自己 Step 3 拿到的；`ZOTERO_LIBRARY_TYPE` 取值 `user`（个人库）或 `group`（共享库）。

这条命令会自动在 `~/.claude.json` 加上：

```json
{
  "mcpServers": {
    "zotero": {
      "command": "zotero-mcp",
      "env": {
        "ZOTERO_LOCAL": "false",
        "ZOTERO_API_KEY": "DXmn3iZsTkQyYk9HHqTIdJdn",
        "ZOTERO_LIBRARY_ID": "20490436",
        "ZOTERO_LIBRARY_TYPE": "user"
      }
    }
  }
}
```

### 方式 B：手动编辑 `~/.claude.json`

打开 `~/.claude.json`（Windows: `%USERPROFILE%\.claude.json`；macOS / Linux: `~/.claude.json`），找到 `mcpServers` 段（不存在就新建），加（**4 个 env 字段全填**）：

```json
"zotero": {
  "command": "zotero-mcp",
  "env": {
    "ZOTERO_LOCAL": "false",
    "ZOTERO_API_KEY": "DXmn3iZsTkQyYk9HHqTIdJdn",
    "ZOTERO_LIBRARY_ID": "20490436",
    "ZOTERO_LIBRARY_TYPE": "user"
  }
}
```

**完全退出 Claude Code 再重启**（关全部 Claude Code 窗口 / 终端再打开）——MCP server 注册不热加载。

> **为什么 `ZOTERO_LOCAL=false` 而不是 `true`**：v0.3.0 的 `ZOTERO_LOCAL=true` + 真实 user ID 的 hybrid 组合**实测不通**——读客户端 `pyzotero(local=True, library_id=20490436)` 拼出 `localhost:23119/api/users/20490436/...`，被 Zotero 本地 API 强拒（`Only data for the logged-in user is available locally -- use userID 0`）。纯 web 模式所有操作都走 `api.zotero.org`，绕开这个本地 API 的 user-ID 0 硬约束。代价：每次 collection list / item search 多一次跨网请求（个人 web API 配额 ~10K req/week，PAI-C 用量远低于此）。
>
> **未来如果 zotero-mcp 修了**这个 hybrid 设计缺陷（让 read client 在 local 模式强制 `library_id=0`），可以改回 `true` 拿性能。届时本 doc 会同步更新。

> **可选：把 key 也设到系统环境变量**（便于 zotero-mcp CLI 直接调用、其他 MCP 客户端共用）：
>
> ```powershell
> [Environment]::SetEnvironmentVariable("ZOTERO_API_KEY", "DXmn3iZsTkQyYk9HHqTIdJdn", "User")
> [Environment]::SetEnvironmentVariable("ZOTERO_LIBRARY_ID", "20490436", "User")
> [Environment]::SetEnvironmentVariable("ZOTERO_LIBRARY_TYPE", "user", "User")
> ```
>
> bash / zsh 用 `export ZOTERO_API_KEY=...` 加进 `~/.bashrc` / `~/.zshrc`。注意：MCP server 仍然依赖 `~/.claude.json` env 块（Claude Code spawn 时通过它传递），系统级 env 是补充而非替代。

## Step 5：验证 PAI-C 集成

进任意 PAI-C 项目跑一次 ingest：

```text
/paic-search motor imagery EEG
ingest 1, 2, 3
```

ingest 跑完最后，**SKILL 应该会问一句**：

```text
检测到 zotero-mcp。本批 3 篇要同步到 Zotero 吗？(y/n，默认 n)
默认 collection: paic-ingest-<YYYYMMDD>
```

回 `y` → SKILL 调 `zotero_create_collection` + 逐篇 `zotero_add_by_doi` / `zotero_add_by_url` / `zotero_add_from_file`，最后渲染同步报告：

```text
同步成功 3/3 篇（collection: paic-ingest-20260506；by_doi: 2, by_arxiv_url: 1）
```

回 Zotero 桌面 app 看 collection 列表，应该多了一个 `paic-ingest-20260506`，里面就是这 3 篇论文。

## 同步语义

### 路由优先级

按论文元数据有什么，SKILL 走第一条命中的路径：

| 优先级 | 条件 | 调的 zotero tool | 行为 |
|---|---|---|---|
| 1 | `paper.doi` 非空 | `zotero_add_by_doi` | Zotero 自动从 CrossRef 抓元数据 + 串联 Unpaywall / arXiv / PMC OA 抓 PDF |
| 2 | `paper.arxiv_id` 非空 | `zotero_add_by_url(https://arxiv.org/abs/<id>)` | Zotero 自动抓 arXiv 元数据 + PDF |
| 3 | 都没有但 `pdf_local_path` 指向已下载的 PDF | `zotero_add_from_file(file_path=<library/pdfs/…绝对路径>, title=…)` | zotero-mcp 先从 PDF 头页 / metadata 抽 DOI——抽到走 `add_by_doi` 拿富元数据 + 附 PDF；抽不到建 `document` item（标题用 `paper.title`）+ 附 PDF |
| 4 | 三条都不可行 | 跳过 | 计入 `zotero_skipped`（reason: `no_doi_no_arxiv_no_pdf`） |

> **优先级 3 的格式约束**：`zotero_add_from_file` 上游白名单 `.pdf / .epub / .djvu / .doc / .docx / .odt / .rtf`。arxiv 论文 SKILL 默认的本地存档是 `.md`（arxiv MCP 抽 markdown），**不会**走优先级 3——它一定有 arxiv_id，走优先级 2 即可。所以优先级 3 主要兜底「s2 / SSRN / google_scholar 等没 DOI 也没 arxiv_id、但用户已经手动 / 或 paper-search-mcp 拿到 PDF」的论文。
>
> **本地 PDF 路径必须是绝对路径**——zotero-mcp 校验 `os.path.isabs(file_path)`，传相对路径会拒收。SKILL 内部用 `pathlib.Path(...).resolve()` 规范化。

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
| **任何 zotero 调用报 `400 Only data for the logged-in user is available locally -- use userID 0`**（URL 形如 `http://localhost:23119/api/users/<id>/...`） | env 是 `ZOTERO_LOCAL=true` + 真实 user ID 的 hybrid 配置——zotero-mcp 把真实 ID 拼进本地 API URL，被 Zotero local API 强拒。**这是「混合模式冲突」的真凶** | 改成纯 web 模式：把 `~/.claude.json` zotero env 里 `ZOTERO_LOCAL` 从 `"true"` 改成 `"false"`；保留其余 3 个 env；完全重启 Claude Code |
| **写操作报 `Cannot perform write operations in local-only mode. Add ZOTERO_API_KEY and ZOTERO_LIBRARY_ID to enable hybrid mode.`** | env 只有 `ZOTERO_LOCAL=true`，没配 API key——读通但写拒 | 按 [Step 3](#step-3申请-zotero-api-key--找-user-id) 申请 API key + user ID；按 [Step 4](#step-4注册到-claude-code) 把 4 个 env 全配齐（`ZOTERO_LOCAL=false`）；完全重启 Claude Code |
| `zotero-mcp --version` 报 `unrecognized arguments` | zotero-mcp 用子命令风格，不是 flag | 改成 `zotero-mcp version`；同理 `setup-info` / `setup` / `serve` 等 |
| `/paic-ingest` 跑完没问 Zotero | zotero-mcp 没注册 / 注册了但 Claude Code 没重启 | 跑 `claude mcp list` 看 zotero 是否在；重启 Claude Code |
| zotero 调用报 `connection refused` | 走的是 `localhost:23119`（说明 env 还是 `ZOTERO_LOCAL=true`）但 Zotero 桌面 app 没开；纯 web 模式不会出这条 | 改成 `ZOTERO_LOCAL=false` 走 web；或启动 Zotero 桌面 app + 勾 Settings → Advanced → Allow other applications |
| `403 Forbidden` / `Invalid key` / `Insufficient permissions` | API key 错 / 没勾 write 权限 / library_id 与 key 不匹配 | curl `https://api.zotero.org/keys/current` 验 `access.user.write: true`；不然回 [Step 3](#step-3申请-zotero-api-key--找-user-id) 重申请并勾 `Allow write access`；group library 时 key 需要对该 group 单独授权 |
| `ZOTERO_LIBRARY_ID not set` / `Missing required environment variables` | env 缺一个字段 | 检查 `~/.claude.json` zotero 段 4 个 env 都有（`ZOTERO_LOCAL` 该是 `"false"`；其余三个是 web 凭据）；重启 Claude Code |
| collection 一直建不上 | 同名 collection race / 权限 | 改名重试；group library 检查 API key 有 read+write 权限 |
| 同一篇被建了副本 | 走了优先级 3（`zotero_add_from_file`）但 zotero-mcp 没从 PDF 头页抽到 DOI，又跟已有的 DOI item 撞上了 | 在 Zotero 里用 `zotero_find_duplicates` / 桌面端 Trash & Duplicate Items 视图 merge；或先给 `selected.yaml` 该篇补上 DOI，重 ingest 走优先级 1 自动去重 |
| SKILL 报 `zotero_skipped: no_doi_no_arxiv_no_pdf` | 论文 metadata 里 doi / arxiv_id 都为空，本地也没下到 PDF | 给 `selected.yaml` 该篇补 DOI 或 arxiv_id 后重 ingest；或先在 Zotero 端手动加 |
| SKILL 报 `local_pdf_unsupported_ext` | `pdf_local_path` 是 `.md`（arxiv markdown）等非白名单格式，但走到了优先级 3 | 该论文应该有 arxiv_id 走优先级 2——检查 `selected.yaml` 是否漏了 arxiv_id；若确实只有 markdown，先用 pandoc 转 PDF 再重 ingest |
| `zotero_add_from_file` 报 `Symlinks are not allowed` 或 `file_path must be an absolute path` | PDF 路径是 symlink 或相对路径 | 把 PDF 实体放到 `library/pdfs/` 下；SKILL 已自动 `Path(...).resolve()`，触发该错通常是用户把 `pdfs/` 软链到外部目录 |

## 进阶

- **改 collection 命名规则**：当前默认 `paic-ingest-<YYYYMMDD>`，SKILL 询问时可以改名或留空（不入 collection）。如果想改默认值——改 `skills/paic-ingest/SKILL.md` 里 step 5.1 的提示模板。
- **多个 Zotero library**（个人 + 多个 group）：当前 zotero-mcp 一次只连一个，由 `ZOTERO_LIBRARY_ID` 决定。要切换 → 改 env 重启。
- **Zotero 端的反向 metadata 抓取**（在 Zotero 改 abstract / venue 后能不能拉回 PAI-C `selected.yaml`）：暂未支持，提需求可加。
