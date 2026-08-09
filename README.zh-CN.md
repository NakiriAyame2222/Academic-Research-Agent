# Academic Research Agent

[English README](README.MD)

一个面向学术研究场景的轻量级 Agent，支持 **论文问答**、**主题调研**、**arXiv 检索** 和 **带引用的回答**。

这个项目可以帮助你：
- 对本地论文或文本文件提问（`.pdf` / `.md` / `.txt`）
- 围绕一个主题调研并收集代表性论文
- 搜索 arXiv 并总结最新工作
- 在多轮会话中保留短期 / 长期记忆
- 输出带来源引用的回答

---

## 项目简介

Academic Research Agent 是一个面向学术工作流的轻量级 Agent，主要能力包括：

- **论文问答**：基于本地文件进行 Paper QA
- **主题调研**：围绕研究主题做文献发现与总结
- **arXiv 检索**：搜索相关论文并生成简要综述
- **引用溯源**：尽量输出带 citation 的回答
- **会话记忆**：支持多轮连续研究

适合用作：
- 论文阅读助手
- 文献调研助手
- 学术 Agent Demo / 作品集项目

## 功能概览

- 支持本地 `.pdf`、`.md`、`.txt` 文件
- 自动将请求路由为：
  - `paper_qa`
  - `topic_research`
- 支持 OpenAI-compatible LLM 与 Embedding 接口
- 支持命令行交互式工作流
- 可选输出 citations / sources

## 安装

```bash
pip install -r requirements.txt
```

## 配置 API / Model / Base URL

本项目使用 **OpenAI-compatible** 接口。你需要提供：

- `LLM_MODEL`
- `API_KEY`
- `BASE_URL`
- `EMBEDDING_MODEL`
- `EMBEDDING_API_KEY`
- `EMBEDDING_BASE_URL`

### 方式 1：推荐，使用默认配置文件

默认配置文件位置：`data/llm_config.env`

示例：

```env
LLM_MODEL=your-llm-model
API_KEY=your-api-key
BASE_URL=https://your-provider-url

EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=your-embedding-api-key
EMBEDDING_BASE_URL=https://your-provider-url
```

### 方式 2：使用仓库里的模板文件

仓库中提供了一个示例模板：[cong.txt](cong.txt)。

填好占位符后，运行：

```bash
python app.py --config-file cong.txt
```

### 方式 3：命令行直接传入

```bash
python app.py --model your-llm-model --api-key your-api-key --base-url https://your-provider-url
```

## 使用方式

### 1. 对本地论文提问

```bash
python app.py "这篇论文的核心贡献是什么？" --file data/papers/your_paper.pdf --show-sources
```

也可以使用 `.md` 或 `.txt` 文件：

```bash
python app.py "请总结这篇文章的方法和实验结果" --file data/papers/your_note.md --show-sources
```

### 2. 进行主题调研

```bash
python app.py "帮我调研一下 llm agent memory 的最新进展"
```

### 3. 启动交互式聊天模式

```bash
python app.py --chat
```

### 4. 恢复历史会话

```bash
python app.py --resume your-session-id
```

## 常用 CLI 参数

- `--file`：指定本地论文或文本文件
- `--chat`：启动交互模式
- `--resume`：恢复已有 session
- `--show-sources`：在回答后打印引用来源
- `--top-k`：控制检索深度
- `--rebuild-index`：强制重建索引
- `--config-file`：从文件加载 model / API / URL 配置
- `--save-config`：保存本次提供的配置

## 支持的后端

当前实现使用 OpenAI-compatible client，因此你可以接入：
- OpenAI-compatible LLM 服务
- OpenAI-compatible Embedding 服务
- 自建或代理的兼容 API 网关

> 如果 API key / model / base URL 配置不正确，项目会回退到 demo 模式，无法生成可靠的论文溯源回答。

## 项目结构

```text
app.py                # CLI 入口
agent/                # agent graph / nodes / artifacts
rag/                  # 检索与引用构建
memory/               # 短期与长期记忆
tools/                # arXiv / search / file tools
data/                 # 配置、论文、向量库、报告
skills/               # research/report 技能定义
tests/                # 测试与 benchmark artifacts
```
