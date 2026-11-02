"""对外暴露本地论文检索能力的 MCP server 包。

目录名不能叫 mcp/ —— 官方 Python SDK 的包名也是 mcp，同名会让 `import mcp`
的解析结果取决于 sys.path 顺序。
"""
