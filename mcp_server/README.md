# Research Agent MCP Server

把本项目的**本地论文检索能力对外暴露成 MCP server**，供 Claude Desktop 之类的客户端调用。
注意方向：这不是"让本 agent 去调别人的 MCP 工具"，而是反过来。

## 暴露的工具

| 工具 | 作用 |
|---|---|
| `search_local_papers(query, top_k)` | 在 `data/papers` 语料库里做混合检索，返回带 `page`/`chunk_index` 定位的证据片段 |
| `list_local_papers()` | 列出可检索的论文文件 |
| `read_local_file(file_path)` | 读取 `data/` 目录内的文件（路径越权会被拒绝） |
| `search_arxiv_papers(query, max_results)` | 按英文关键词检索 arXiv |
| `get_paper_notes(topic, limit)` | 读取 agent 此前写进 SQLite 的论文笔记 |

索引与 CLI 主流程**共用同一份**：`data_dir=data/papers`、`persist_dir=data/vector_store/corpus`。

## 启动

```bash
pip install fastmcp

# 默认 stdio（Claude Desktop 用这个）
python -m mcp_server.server

# 或通过主 CLI
python app.py --serve-mcp

# HTTP 传输
python -m mcp_server.server --transport http --host 127.0.0.1 --port 8000

# 首次使用或新增了论文文件时重建索引
python -m mcp_server.server --rebuild-index
```

## Claude Desktop 配置

`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "research-agent": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "D:/C/简历文件/agent-project-research",
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "RESEARCH_AGENT_EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-4B",
        "RESEARCH_AGENT_EMBEDDING_API_KEY": "<your-key>",
        "RESEARCH_AGENT_EMBEDDING_BASE_URL": "https://api-inference.modelscope.cn/v1/"
      }
    }
  }
}
```

没有 embedding 配置也能用，检索会降级为纯稀疏（TF-IDF）模式。
