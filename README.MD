# Academic Research Agent · 学术研究智能体

[English README](README.en.md) ｜ [演示视频 demo.mp4](demo.mp4) ｜ [示例报告](data/reports/demo.md)

面向学术场景的**全栈 Deep Research 智能体**：上传论文即可精读问答；输入一个研究主题，智能体会自动拆解子主题、并发检索 arXiv、下载并精读全文，生成**带引用审计**的结构化研究报告；报告完成后还可多轮追问、按需补充取证。内置开箱即用的 Web 界面、会话记忆体系与 MCP 扩展。

## 演示视频

<video src="demo.mp4" controls width="100%"></video>

> 无法内嵌播放时，可 [下载 demo.mp4](demo.mp4) 观看。视频演示了一次完整的深度调研流程：主题规划 → 多子主题并发取证 → 实时进度流 → 运行中动态指令 → 报告生成与引用高亮 → 报告讨论。

## 核心特性

- **论文问答（Paper QA）**：上传 PDF / MD / TXT 后对单篇论文提问；回答带 `[Sx]` 证据编号，可溯源到页码、章节与文本块。
- **主题调研（Deep Research）**：LLM 规划检索计划并把主题拆成子主题，调度器对每个子主题独立执行「arXiv 元数据检索 → 候选去重与筛选 → PDF 下载与全文精读 → 结构化笔记」，最后聚合生成综述报告。
- **报告多轮讨论**：报告完成后继续追问，自动判定追问意图（报告内问题 / 需要补充证据 / 开启新调研），按需回到已有全文索引取证，不会重复下载。
- **运行中动态控制**：调研进行中可随时发送自然语言指令——增加调研主题、取消某个子主题、取消整个任务、恢复中断任务；指令歧义时返回候选列表供澄清。
- **中断恢复**：子主题级 checkpoint，服务重启后可只续跑未完成的主题，已完成的成果全部保留。
- **引用溯源与审计**：证据池统一编号（S1、S2…），报告生成后自动校验引用有效性、标注无效引用、补全 Sources 段，并输出 `valid_reference_rate` 等审计指标。
- **混合检索**：Chroma 向量索引 + TF-IDF 稀疏检索（jieba 中文分词），支持语料库 / 单文件 / 会话级三种索引作用域；无 Embedding API 时自动降级为纯稀疏检索。
- **记忆体系**：短期 JSON checkpoint + 长期 SQLite（用户记忆、论文笔记、会话产物，按向量/关键词/时效排序召回）；超长对话自动 LLM 压缩；长期记忆候选在 Web 端确认后入库。
- **Web 界面**：React SPA，SSE 实时进度（子主题状态卡片、工具调用轨迹、反思结论），报告渲染与引用高亮，论文上传与论文库管理，在线模型设置。
- **MCP Server 扩展**：把本地论文库检索能力暴露给 Claude Desktop 等 MCP 客户端。
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

### 1. 安装依赖

要求 Python 3.10+。

```bash
pip install -r requirements.txt
```

其中 `chromadb` 与 `jieba` 为可选增强：未安装时检索自动降级为内置存储 / 纯稀疏模式，功能不受影响。

### 2. 配置模型

推荐使用默认配置文件 `data/llm_config.env`（仓库已提供模板，把占位符替换为真实值即可）：

```env
LLM_MODEL=your-llm-model
API_KEY=your-api-key
BASE_URL=https://your-provider-url

Embedding_MODEL=your-embedding-model
Embedding_API_KEY=your-embedding-api-key
Embedding_BASE_URL=your-embedding-provider-url
```

也可以启动时传入配置：

```bash
python app.py --config-file your-config.env
```

或启动后在 Web 界面右上角「设置」中在线填写（密钥掩码显示，保存后写入 `data/llm_config.env` 并立即生效）。

没有 Embedding 配置也能运行（自动降级为稀疏检索）；没有 LLM key 时进入演示降级模式，会明确提示而不是编造答案。

### 3. 启动

```bash
python app.py
# Web UI: http://127.0.0.1:8000 （按 Ctrl+C 退出）
```

`app.py` 参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--web-host` | `127.0.0.1` | 监听地址 |
| `--web-port` | `8000` | 监听端口 |
| `--config-file` | - | 从 KEY=VALUE 文件加载模型配置 |

部署形态为单进程：FastAPI 直接托管 `web/dist` 构建产物，线上不需要 Node。Chroma 与任务调度共用本地状态，因此固定单 worker。

## Web 界面

- **左侧会话列表**：新建 / 切换 / 删除研究会话，刷新后自动恢复上次会话。
- **查询栏**：输入研究主题开始调研；上传 PDF/MD/TXT（或从论文库选择）后自动进入单篇论文问答；可设置检索数量 `top_k`、是否重建所选论文索引。
- **调研进行中**：进度日志实时滚动，各子主题以卡片形式展示状态（排队 / 运行中 / 完成 / 失败 / 取消）；查询栏自动切换为「指令模式」，可直接发送动态指令。
- **结果区**：Markdown 报告渲染，点击 `[Sx]` 引用高亮右侧对应来源；支持下载报告 Markdown。
- **右侧边栏**：来源证据、每篇论文的取证状态、工具调用轨迹（按需展开）、反思结论、待确认长期记忆候选（勾选保存）。
- **中断恢复**：检测到未完成的调研时显示「继续执行」提示条，保留已完成成果并只续跑未完成主题。
- **设置对话框**：在线修改 LLM / Embedding 模型与密钥、最大研究轮次、每轮论文数；健康条展示后端能力状态（LLM 可用性、tool calling、向量后端等）。

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
| 长期记忆 | SQLite（`data/memory.db`） | 用户确认后的记忆条目，按向量 + 关键词 + 时效加权召回 |
| 论文笔记 | SQLite | 每篇精读论文的结构化笔记（问题/方法/实验/总结） |
| 上下文压缩 | LLM | 中段消息摘要压缩，保住初始问题与近期对话 |
| 输出偏好 | 规则识别 | 从本轮 query 识别语言 / 格式 / 语气要求 |

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
├── skills/                   # 轻量 YAML 工作流定义（文献综述 / 报告生成）
├── web/                      # React 前端（Vite + Tailwind），详见 web/README.md
└── data/                     # 运行时数据
    ├── llm_config.env        # 模型配置（模板已随仓库提供）
    ├── papers/               # 本地论文库
    ├── vector_store/         # 检索索引（corpus / files / sessions）
    ├── checkpoints/          # 会话 checkpoint
    ├── conversations/        # 对话记账
    ├── reports/              # 生成的报告（demo.md 为真实生成示例）
    └── memory.db             # 长期记忆
```


## License

[MIT](LICENSE)
