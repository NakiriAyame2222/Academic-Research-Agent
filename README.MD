# Academic Research Agent · 学术研究智能体

[English README](README.en.md) ｜ [演示视频 demo.mp4](demo.mp4) ｜ [示例报告](data/reports/demo.md)

面向学术场景的**全栈 Deep Research 智能体**：上传论文即可精读问答；输入一个研究主题，智能体会自动拆解子主题、检索 arXiv、下载并精读全文，生成**带引用审计**的结构化研究报告；报告完成后还可多轮追问、按需补充取证。内置开箱即用的 Web 界面、会话记忆体系与 MCP 扩展。

## 演示视频

https://github.com/user-attachments/assets/9d3b3449-e64d-493e-8bb5-715303a6c3be

> 无法内嵌播放时，可 [下载 demo.mp4](demo.mp4) 观看。视频演示了一次完整的深度调研流程：主题规划 → 多子主题并发取证 → 实时进度流 → 运行中动态指令 → 报告生成与引用高亮 → 报告讨论。

## 核心特性

- **论文问答（Paper QA）**：上传 PDF / MD / TXT 或复用论文库文件后对单篇论文提问；回答带 `[Sx]` 来源引用，可溯源到页码、章节与文本块。
- **主题调研（Deep Research）**：输入研究主题，按子主题顺序执行「arXiv 检索 → 精选去重 → PDF 下载与全文精读 → 结构化笔记」，聚合生成结构化研究报告；运行中可动态追加、取消子主题，服务中断后可从 checkpoint 恢复继续执行。
- **报告多轮讨论**：报告完成后继续追问，自动判定追问意图（报告内问题 / 需要补充证据 / 开启新调研），按需回到已有全文索引取证，不会重复下载。
- **引用溯源与审计**：证据池统一编号（S1、S2…），报告生成后自动校验引用有效性、标注无效引用、补全 Sources 段，并输出 `valid_reference_rate` 等审计指标。
- **混合检索**：Chroma 向量索引 + TF-IDF 稀疏检索（jieba 中文分词）；无 Embedding API 时自动降级为纯稀疏检索，向量索引可随时重建，SQLite 是事实源。
- **记忆体系**：短期会话压缩、已保存研究笔记召回、论文笔记存档与子主题级 checkpoint，正式数据统一存入 `data/app.db`。
- **Web 单一入口**：React SPA + SSE 实时进度（子主题状态、工具调用轨迹、反思结论），报告渲染与引用高亮；「研究设置」配置模型、检索默认值与输出偏好，自动化复用同一套 HTTP API。
- **MCP Server 扩展（可选）**：把本地论文库检索能力暴露给 Claude Desktop 等 MCP 客户端，单独安装 `requirements-mcp.txt`。
- **优雅降级**：端点不支持 tool calling / JSON mode 时自动脱参并切换确定性取证流程；无 LLM key 时进入明确的演示模式，不伪造答案。

## 系统架构

```
 web/ 前端 (React 18 + Vite + Tailwind)
   会话侧栏 · 查询/上传栏 · SSE 进度日志 · 报告视图+引用高亮 · 证据/工具轨迹/记忆面板
        │  REST + SSE（submit → 进度流 → 结果）
        ▼
 api/ FastAPI 适配层
   JobManager 线程池执行阻塞任务，事件经 loop.call_soon_threadsafe 投递给 SSE
   /api/sessions · /api/jobs · /api/papers · /api/settings · /api/health
        │
        ▼
 agent/ 智能体核心
   runtime.py            入口：意图路由 → paper_qa / topic_research / report_discussion
   graph.py (LangGraph)  planner → evidence → researcher → reflect ⇄ → writer → memory
   topic_scheduler.py    多子主题调度：并发取证 · 动态指令 · 失败重试 · 中断恢复 · 聚合写报告
   tool_loop.py          OpenAI tool-calling 取证循环（不支持时走确定性流程）
   citation_check.py     引用审计：有效性校验 · 无效标注 · Sources 段补全
        │                     │                        │
        ▼                     ▼                        ▼
 tools/ 工具层          rag/ 检索增强              memory/ 记忆
 arXiv 检索/限速/下载   PDF 解析 · 分块 · 建索引   短期 checkpoint (JSON)
 语料检索 · 工具注册表  Chroma / TF-IDF 稀疏降级   长期 SQLite · 压缩 · 偏好
```

## 快速开始

### 1. 安装与启动

要求 Python 3.10+。仓库已附带前端构建产物（`web/dist`），仅使用时无需 Node：

```bash
python -m pip install -r requirements.txt
python app.py
# 打开 http://127.0.0.1:8000
```

修改前端源码后需重新构建：

```bash
npm --prefix web ci
npm --prefix web run build
```

### 2. 配置模型

复制 `data/llm_config.env.example` 为 `data/llm_config.env`（已被 .gitignore 忽略，不会上传），填入你的模型与 API Key；也可以启动后在 Web「研究设置」中直接填写，保存后写入 `data/llm_config.env` 并立即生效。

已有 KEY=VALUE 配置文件可在启动时加载：

```bash
python app.py --config-file path/to/model.env
# 自定义监听地址与端口
python app.py --web-host 127.0.0.1 --web-port 8080
```

没有 Embedding 配置也能运行（自动降级为稀疏检索）。`chromadb` 与 `jieba` 为可选增强：未安装时检索自动降级为内置存储 / 纯稀疏模式。

部署形态为单进程：FastAPI 直接托管 `web/dist` 构建产物，线上不需要 Node；Chroma 与任务调度共用本地状态，固定单 worker。开发时前后端分别启动的方式见 [前端说明](web/README.md)，Docker 构建后同样只运行 Web 服务，持久化目录为 `/app/data`。

## Web 界面

- **调研方向**：输入课题开始调研，运行中可追加、取消子主题；重新打开页面可查看进度，服务中断后可选择继续执行。
- **研读论文**：「上传论文」支持 PDF、MD、TXT；「已有论文」可复用论文库中的文件。选中后问题围绕该论文回答，移除选择后可调研其他方向。
- **检索控制**：「本次检索设置」可覆盖本轮检索数量；选中论文后可勾选开始前重建索引。默认检索数量在「研究设置」保存。
- **留存成果**：查看引用、下载 Markdown 结果、主动保存研究笔记；历史会话会恢复所选论文，方便继续追问。

## 三种工作模式

### 论文问答 Paper QA
上传或选择本地论文 → 为该文件建立独立索引 → tool-calling 取证循环在论文内检索证据（`search_local_papers` / `read_local_file`）→ 输出带 `[Sx]` 引用的回答。

### 主题调研 Topic Research
LLM 生成检索计划与查询批次 → 调度器拆分子主题并发执行：arXiv 元数据检索 → 综述论文搜索 → 候选规范化与去重（上限每子主题 30 条、全局精选 60 篇）→ LLM 精选 → 下载全文并精读做笔记 → 反思证据是否充分（不足则补充检索，最多 `MAX_RESEARCH_ROUNDS` 轮）→ 聚合所有子主题证据 → 大纲 → 分章节写作 → 引用审计 → 报告落盘为 Markdown。

### 报告讨论 Report Discussion
对已完成的报告继续提问：自动分类追问意图，检索范围限定在「当前报告 + 已选论文笔记 + 按需全文证据」，新增证据续接原引用编号，多轮讨论保留在会话中。

## 运行中动态控制

调研进行中对查询栏输入自然语言指令即可（也可走结构化 JSON）：

| 指令示例 | 效果 |
|---|---|
| `增加调研主题 扩散模型在蛋白质结构预测的应用` | 新增一个子主题并加入调度 |
| `取消调研主题 b` / `取消主题 <名称>` | 取消指定子主题；匹配到多个候选时返回澄清列表 |
| `取消整个任务` / `停止任务` | 协同终止调度器与各子主题工作线程 |
| `恢复中断的调研` / `继续` | 从 checkpoint 恢复，只续跑未完成主题 |

每个子主题失败自动重试（默认 2 次）；报告聚合前取消的主题会从本轮报告中移除。

## 记忆与上下文

| 层 | 实现 | 内容 |
|---|---|---|
| 短期记忆 | JSON checkpoint（`data/checkpoints/`） | 完整会话状态，支撑多轮续跑与中断恢复 |
| 对话记账 | JSON（`data/conversations/`） | 每轮 query + 完整结果，供 Web 历史展示 |
| 长期记忆 | SQLite（`data/app.db`） | 已保存的记忆条目，按向量 + 关键词 + 时效加权召回 |
| 论文笔记 | SQLite | 每篇精读论文的结构化笔记（问题/方法/实验/总结） |
| 上下文压缩 | LLM | 中段消息摘要压缩，保住初始问题与近期对话 |
| 输出偏好 | 研究设置 | Web「研究设置」保存语言 / 格式 / 风格 / 深度默认值，本轮要求可临时覆盖 |

## 检索与引用

- **解析与分块**：pypdf + PyMuPDF 双引擎解析 PDF，自动识别章节标题，按 1000/200 重叠分块。
- **索引后端**：`auto` 模式优先 Chroma，未安装或无 Embedding 时降级为内置向量存储 + TF-IDF 稀疏检索（jieba 分词）。
- **引用系统**：证据进入统一证据池并分配 `S` 编号；写作强制每个核心论断带 `[Sx]`；落盘前审计——无效引用会被标注，Sources 段自动补全，审计指标随结果返回。

## REST API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 能力探针：LLM 可用性、tool calling、向量后端、降级提示 |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/{id}` | 会话详情（含最近一次研究结果） |
| DELETE | `/api/sessions/{id}` | 删除会话及其对话日志 |
| GET | `/api/sessions/{id}/conversation` | 多轮对话历史 |
| POST | `/api/sessions/{id}/research` | 提交研究任务，立即返回 `202 {job_id}` |
| GET | `/api/sessions/{id}/memories` | 待确认的长期记忆候选 |
| POST | `/api/sessions/{id}/memories` | 勾选保存长期记忆 |
| GET | `/api/sessions/{id}/report` | 下载该会话的报告 Markdown（限制在 `data/` 内） |
| GET | `/api/jobs/{job_id}` | 任务视图：状态 / 子主题 / 指令记录 / 结果（SSE 之外的轮询兜底） |
| GET | `/api/jobs/{job_id}/events` | SSE 进度流：`progress` / `done` / `error` / `cancelled`，支持断线重连续传 |
| POST | `/api/jobs/{job_id}/cancel` | 取消任务 |
| GET / POST | `/api/settings` | 查看 / 更新模型与检索配置（密钥掩码返回） |
| POST | `/api/papers/upload` | 上传论文（白名单扩展名，≤ 50MB，文件名安全清洗） |
| GET | `/api/papers` | 列出本地论文库 |

研究请求体（`POST /api/sessions/{id}/research`）：`query`、`file_path`（可选，进入 Paper QA）、`top_k`（1–20）、`rebuild_index`、`resume`（会话续跑）。

## MCP Server 扩展

把本地论文库能力反向暴露成 MCP server，供 Claude Desktop 等客户端调用（详见 [mcp_server/README.md](mcp_server/README.md)）：

| 工具 | 作用 |
|---|---|
| `search_local_papers(query, top_k)` | 在论文库中做混合检索，返回带页码/chunk 定位的证据片段 |
| `list_local_papers()` | 列出可检索的论文文件 |
| `read_local_file(file_path)` | 读取 `data/` 内的文件（路径越权会被拒绝） |
| `search_arxiv_papers(query, max_results)` | 按英文关键词检索 arXiv |
| `get_paper_notes(topic, limit)` | 读取智能体此前写入 SQLite 的论文笔记 |

```bash
pip install -r requirements-mcp.txt
python -m mcp_server.server                      # 默认 stdio（Claude Desktop）
python -m mcp_server.server --transport http     # HTTP 传输
python -m mcp_server.server --rebuild-index      # 重建论文库索引后启动
```

## 项目结构

```
├── app.py                    # 入口：启动 uvicorn（API + 托管 web/dist）
├── config.py                 # 配置加载、LLM/Embedding 客户端、能力探测与降级
├── prompts.py                # 全部 LLM 提示词
├── agent/                    # 智能体核心
│   ├── runtime.py            # run_research 入口：意图路由 / 初始化 / 报告讨论
│   ├── graph.py              # LangGraph 图（含反思循环）
│   ├── nodes.py              # planner / evidence / researcher / reflect / writer / memory 节点
│   ├── topic_scheduler.py    # 多子主题调度器（动态指令、重试、恢复、聚合写报告）
│   ├── tool_loop.py          # tool-calling 取证循环
│   ├── deep_research_pipeline.py  # 候选规范化/去重/选择/评审解析 + 阶段计时
│   ├── report_discussion.py  # 报告追问：意图分类、按需取证、上下文构建
│   ├── citation_check.py     # 引用审计
│   ├── research_artifacts.py # working document：检索/笔记/大纲/章节过程追踪
│   ├── intent.py             # paper_qa / topic_research 意图打分（中英双语）
│   ├── llm.py / progress.py / state.py / json_utils.py / research_service.py
├── api/                      # FastAPI 适配层（不改动 agent 核心）
│   ├── jobs.py               # JobManager：线程池执行 + SSE 事件流 + 指令通道
│   ├── routes/               # sessions / jobs / papers / settings / health
│   └── schemas.py / serialize.py / conversation.py / fake.py
├── tools/                    # arXiv 检索下载、本地语料检索、工具注册表、文件读取
├── rag/                      # 文档解析分块、检索编排、Chroma / 稀疏后端、分词
├── memory/                   # 短期 checkpoint、长期 SQLite、压缩、偏好、笔记
├── mcp_server/               # MCP 扩展（对外暴露论文库）
├── storage/                  # SQLite 存储层（database / repositories / outbox / worker）
├── scripts/                  # 存储迁移与真实调研验收脚本
├── docs/                     # 设计与调研文档
├── web/                      # React 前端（Vite + Tailwind），详见 web/README.md
└── data/                     # 运行时数据（已被 .gitignore 忽略，demo.md 报告除外）
    ├── llm_config.env        # 模型配置（模板 llm_config.env.example 已随仓库提供）
    ├── app.db                # 正式 SQLite 数据库（会话 / 记忆 / 论文笔记）
    ├── user_files/           # 用户文件（papers/ 上传论文库，downloads/ 下载全文）
    ├── chroma/               # 向量索引（可重建，SQL 是事实源）
    ├── checkpoints/          # 会话 checkpoint
    ├── conversations/        # 对话记账
    └── reports/              # 生成的报告（demo.md 为真实生成示例）
```


## License

[MIT](LICENSE)
