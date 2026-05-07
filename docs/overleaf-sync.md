# Overleaf 双向同步

> **Optional** · `/paic-draft` 跑完后基于 Dropbox 把 `drafts/` 与 Overleaf project 双向同步 —— 免 API key、免 Overleaf Premium。

PAI-C 在 `/paic-draft fill` / `polish` / `compose` 跑完后**可选**把 `<project>/.paic/drafts/` 与一个 Overleaf project 双向同步。机制是 Overleaf 后台的 **Dropbox 集成**——免 API key、免 Overleaf Premium git integration——PAI-C 把 drafts 镜像到 `~/Dropbox/Apps/Overleaf/<project>/`，Dropbox 客户端自动上传到 Overleaf；反向 Overleaf 上的改动通过 Dropbox 流回本地、PAI-C 用三方 merge 合并。

> 状态文件位置：`<project>/.paic/state/overleaf_sync.yaml`（baseline manifest，记录上一次成功 sync 的快照，用来区分本地新改 vs Overleaf 新改）。

## 适用场景

- 主力在本地用 Claude Code 写论文，但需要把 Overleaf 当编译器 / PDF 预览（免装本地 LaTeX 工具链）
- 跟合作者共享 Overleaf project，他们在网页端改 abstract / refs，你在本地 PAI-C 续写正文，双方互不覆盖
- 提交 deadline 前要打开 Overleaf 看实时编译错误 + 排版

不适合：单人本地完整 LaTeX 工具链（直接 `latexmk` 编译就行，不需要 Overleaf）。

## 前置依赖

- **Overleaf 账号**（任何 plan 都行，含 Free——Dropbox 集成是免费档自带的）
- **Dropbox 账号 + 桌面客户端**（任何 plan，2 GB Free 足够典型论文项目）
- 本地 `~/.paic/config.yaml`（已通过 `register_mcp.py` 创建）

## Step 1：装 Dropbox 桌面客户端

1. 从 [dropbox.com/install](https://www.dropbox.com/install) 下载装上、登录。
2. 确认本地有 Dropbox 文件夹：
   - **Windows**：默认 `%USERPROFILE%\Dropbox`，即 `C:\Users\<you>\Dropbox`
   - **macOS / Linux**：默认 `~/Dropbox`
3. **保持 Dropbox 客户端在后台运行**——它是 PAI-C ↔ Overleaf 之间的传输管道，关了双向同步就停了。

## Step 2：在 Overleaf 后台连接 Dropbox（关键一步）

1. 登录 [overleaf.com](https://www.overleaf.com)。
2. 右上角头像 → **Account Settings**。
3. 左侧 **Integrations** → **Linked Accounts**。
4. **Dropbox** 行点 **Link** → 跳转 Dropbox 授权 → 同意。
5. **回到本地等约 30 秒**，Dropbox 客户端会同步出 `~/Dropbox/Apps/Overleaf/` 文件夹（首次连接才创建，可能要 1-2 分钟）。

**验证**：打开本地 `~/Dropbox/Apps/Overleaf/`，能进就 OK——里面初始为空，等 PAI-C 第一次 push 后才会出现 project 子目录。

> 如果你已经在 Overleaf 里有 project，连接 Dropbox 后 Overleaf 会把现有 project 也镜像到这个目录里。

## Step 3：在 PAI-C 配置 overleaf 段

打开 `~/.paic/config.yaml`，找到（或新增）`overleaf:` 段：

```yaml
overleaf:
  enabled: true
  target_root: ~/Dropbox/Apps/Overleaf       # Windows 上 ~ 自动展开为 %USERPROFILE%
  project_subdir: null                       # null = 用 PAI-C 项目目录的 basename；想叫别的就填这里
  conflict_strategy: keep_both               # keep_both | local_wins | remote_wins | newer_wins
  prompt_on_delete: true                     # 单边删除是否传播
  ignore_patterns:                           # fnmatch 通配；编译产物类
    - "*.pdf"
    - "*.aux"
    - "*.log"
    - "*.out"
    - "*.bbl"
    - "*.blg"
    - "*.synctex.gz"
    - "*.toc"
    - "*.nav"
    - "*.snm"
    - "*.bak.*"
    - ".DS_Store"
    - "Thumbs.db"
```

完整字段说明见 [`docs/config.yaml.example`](config.yaml.example) 的 `overleaf:` 段。

**改完完全退出 Claude Code 再重启**——MCP server 不会热加载 yaml。

## Step 4：验证

```powershell
uv run paic doctor
```

应看到：

```text
overleaf sync                ok      enabled=true | target_root=C:\Users\<you>\Dropbox\Apps\Overleaf | strategy=keep_both | prompt_on_delete=True
```

如果是 `warn: target_root=... (missing)`——回 Step 2，确认 Overleaf 后台 Dropbox 已连且本地 `~/Dropbox/Apps/Overleaf/` 文件夹真存在。

如果是 `skip: disabled`——`overleaf.enabled` 还是 `false`，改成 `true` 重启。

## Step 5：使用流程

进任意 PAI-C 项目目录（如 `C:\Users\<you>\Desktop\my_paper`），跑 LaTeX 阶段：

```text
/paic-draft fill --template neurips ...
```

`fill` 跑完，SKILL 会问：

```text
检测到 Overleaf 配置。要把 drafts/ 同步到 Overleaf 吗？
目标：~/Dropbox/Apps/Overleaf/my_paper/
即将 push: 5 个文件（main.tex, refs.bib, sections/01_intro.tex, ...）
（y/n，默认 n）
```

回 `y` → PAI-C 调 `paic_draft_sync_overleaf` → 文件 push 到 Dropbox →（5-30 s 后）登 [overleaf.com](https://www.overleaf.com)，**Project 列表**里会出现新 project `my_paper`，里面就是你的 main.tex / refs.bib / sections/。

后续每次 `polish` / `compose` 跑完同样会问要不要 sync。

## 反向同步：在 Overleaf 改完拉回本地

在 Overleaf 网页编辑 `sections/01_intro.tex`（或上传图片到 `figures/`），保存。回到本地跑：

```text
/paic-draft polish ...
```

或下次任何 `/paic-draft` 跑完，SKILL 会先做一次 `dry_run` 同步，显示：

```text
sync 预览（dry_run）：
  pulled: 1 (sections/01_intro.tex)
  pushed: 0
  conflicts: 0
确认同步？(y/n)
```

回 `y` → Overleaf 端的改动拉回本地 → 接着进入 polish。这样就保持了「Overleaf 端改 ↔ 本地改」交替进行而不丢任何一方的工作。

## 冲突策略选择

两边在同一文件、同一区间都有改 → 触发冲突。`conflict_strategy` 决定怎么处理：

| 策略 | 行为 | 适用场景 |
|---|---|---|
| `keep_both`（默认，**推荐**） | 本地保留原状；Overleaf 端版本被拉到本地为 `<name>.overleaf-conflict.<UTC>.<ext>`，让你用 diff 工具手动 merge | 不知道选什么时用这个；冲突可见且无损 |
| `local_wins` | 本地覆盖 Overleaf 端 | 主要在本地用 PAI-C，Overleaf 仅做编译预览 |
| `remote_wins` | Overleaf 端覆盖本地 | 主要在 Overleaf 改，PAI-C 偶尔续写 |
| `newer_wins` | 按 mtime 哪边新留哪边 | 都不太关心；**慎用**——跨 OS / Dropbox 客户端 mtime 偶尔不准 |

> `keep_both` 是非破坏性的——任何一边的改都不会无声丢失。**强烈推荐保持默认**，merge 完手动删掉 `.overleaf-conflict.*` 文件即可。

## 删除策略

单边删除（一边删了文件，另一边还在）默认**不会**自动传播——避免「我只是没在 Overleaf 上看到这个 .tex，就以为可以删，结果 sync 把本地的也删了」之类的事故。

| `prompt_on_delete` | 行为 |
|---|---|
| `true`（默认） | 单边删除进 `deletions_pending`，SKILL 询问后才传播；用户确认 = 删掉，拒绝 = 把删除一方恢复回来 |
| `false` | 永不传播删除（最保守）；删了的文件会被另一方"复活"回去 |

如果你想删除某个文件就是想删，确认时回 `y` 即可；如果只是误删的、还想保留，回 `n` 让 PAI-C 复活它。

## 常用 sync 选项

`paic_draft_sync_overleaf` 接受以下可选参数（SKILL 会问你要不要传）：

- `dry_run: true` — 只看会做什么，不真的动文件（首次同步前**强烈建议**先跑一次）
- `direction: auto | push_only | pull_only` — `push_only` 仅推本地→远端、`pull_only` 仅拉远端→本地、`auto` = 双向（默认）
- `confirm_deletions: true` — 显式确认要传播 `deletions_pending` 里的删除（与 `prompt_on_delete: true` 一起用）
- `target: <path>` — 一次性覆盖 `target_root`，用于临时同步到非默认位置

## 故障排除

| 症状 | 原因 | 处理 |
|---|---|---|
| `paic doctor` 报 `target_root=... (missing)` | Overleaf 后台没连 Dropbox / 连了但本地客户端还没同步出来 | 回 Step 2 重连；等 1-2 分钟让 Dropbox 客户端创建文件夹 |
| `paic doctor` 报 `overleaf sync skip: disabled` | `overleaf.enabled` 是 `false` | 改成 `true`，重启 Claude Code |
| `/paic-draft fill` 跑完没问 sync | `enabled=false` / yaml 改了没重启 | 检查 yaml + 完全重启 Claude Code |
| Overleaf 上看不到 push 的 project | Dropbox 客户端没运行 / 网络阻塞 / Overleaf 端同步队列还在排 | 等 1-2 分钟；右下角 Dropbox 图标看是不是「已同步」；浏览器刷新 Overleaf project 列表 |
| 文件名出现 `<file> (conflicted copy YYYY-MM-DD).tex` | 这是 **Dropbox 自身**的冲突文件（多设备同时写同一文件触发，与 PAI-C 无关） | diff + 保留正确版本 + 删另一份 |
| 文件名出现 `<file>.overleaf-conflict.<UTC>.tex` | PAI-C 三方 merge 检测到冲突，用 `keep_both` 策略保了 Overleaf 端版本 | diff 它和本地原文件，merge → 删掉 `.overleaf-conflict.*` → 再 sync 一次 |
| sync 报 `error: drafts_dir_not_found` | `<project>/.paic/drafts/` 还不存在 | 先跑 `/paic-draft fill` 起骨架 |
| 单边删除被复活 | `prompt_on_delete: true` + 上次 sync 时回了 `n` | 这次 sync 时回 `y` 确认删除；或临时改 `prompt_on_delete: false` 也无效——它**只**关闭传播，不会主动删 |
| Overleaf 编译报 `! LaTeX Error: File X.sty not found` | 静态 `.sty` / `.cls` 没被 push | 检查 ignore_patterns 没把它们排掉；`fill` 应该已经 scaffold 进 drafts，跑一次 sync 让它们上去 |

## 进阶

### 多个 PAI-C 项目对应同一个 Overleaf project

`project_subdir: my_overleaf_proj` 显式指定子目录名，覆盖默认的「PAI-C 项目目录 basename」。这样不同的本地 PAI-C 项目都能 push 到同一个 Overleaf project（**只是它们会互相覆盖**——通常不是你想要的，更常见的用途是反过来：一个 PAI-C 项目固定推到某个早就建好的 Overleaf project）。

### 跨 PAI-C 实例共享 Overleaf

baseline manifest 在 `<project>/.paic/state/overleaf_sync.yaml`，与项目目录绑定。两台机器各自的 PAI-C 项目都连同一个 Overleaf project → 各自维护一份 baseline → 在另一台机器上 sync 时会把对方推上去的改动当成「Overleaf 端新改」拉下来——**这是预期行为**，三方 merge 仍然能正确处理冲突。

### 关闭整个功能

`overleaf.enabled: false`——SKILL 不再询问，`paic doctor` 报 `skip`。这是不删 yaml 段的临时关闭方式（保留配置以备后用）。
