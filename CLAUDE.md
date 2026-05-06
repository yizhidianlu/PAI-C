# PAI-C — Claude Code 项目级指令

## 语言

- 与用户对话用中文。
- LLM 生成的论文产出（idea / review / experiment plan / LaTeX 段落）一律英文。
- 单元测试断言、日志、commit message 用英文。

## 架构边界

- **PAI-C MCP server** 不反向调 arxiv MCP。Skill 层用 prompt 编排两个 MCP 协同。
- **LangGraph state** 全部走 `SqliteSaver`，路径 `<project>/.paic/state/checkpoints.sqlite`。绝不在内存里维护跨工具调用的 graph state。
- **长 run 用 fire-and-poll**：`*_start` 立即返回 `run_id`；后台 `asyncio.create_task` 跑 graph；`*_status` 轮询；`*_step` / `*_resume` 推进。
- **LLM 调用统一走 `paic.llm.router.LLMRouter`**：每个调用站点声明 `node` 标签，由 router 按 `~/.paic/config.yaml` 的 `routing.default + routing.overrides` 路由到 4 个 backend 之一（`anthropic.api_key` / `anthropic.claude_agent_sdk` / `openai.api` / `openai.compatible`）。新增 LLM 调用必须给出 node 标签，并在 `docs/config.yaml.example` 同步注释。

## 路径解析

- 全局工作区：`~/.paic/`
- 项目工作区：`<cwd 或上溯找到的最近 .paic>/`，所有 MCP 工具的 `project_dir` 参数都指向这个

## 测试

- `uv run pytest` 通过；schema/dedupe/review_graph 三类必须有单测
- mock LLM 调用，避免真实 API 出现在 CI 上

## 不做的事

- 不重写 arXiv 检索 / 下载 / 本地向量索引（用 arxiv MCP）
- 不在 MVP（v0.1）里跑 LaTeX 编译；只产 .tex 文本
- 不支持中文论文 / 中文 LaTeX 输出

## 文档

- 用户面文档以 [`docs/`](docs/) 为权威；改动 SKILL 行为、配置 schema、CLI 命令时要同步对应的 `.md`
- README 「Status」段是公开发版亮点，不放路线图细节
