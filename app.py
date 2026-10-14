from __future__ import annotations

import argparse

from config import apply_config_values, load_kv_config_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Academic Research Agent Web Server",
        epilog="请在网页中提问、选择论文和管理会话。MCP 扩展使用 python -m mcp_server.server 启动。",
    )
    parser.add_argument("--web-host", default="127.0.0.1", help="Web service host")
    parser.add_argument("--web-port", type=int, default=8000, help="Web service port")
    parser.add_argument("--config-file", help="Load model config from a KEY=VALUE file")
    args = parser.parse_args()

    if args.config_file:
        apply_config_values(load_kv_config_file(args.config_file))

    import uvicorn

    print(f"Web UI: http://{args.web_host}:{args.web_port}  （按 Ctrl+C 退出）")
    uvicorn.run(
        "api.main:app",
        host=args.web_host,
        port=args.web_port,
        workers=1,  # Chroma 与任务调度共用本地状态，使用单进程。
    )


if __name__ == "__main__":
    main()
