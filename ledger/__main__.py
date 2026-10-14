"""入口分发：`uv run python -m ledger serve` = UI 常驻；默认 = MCP stdio。

口径来源：架构设计方案 v1.0 §2.3/§2.4/§4。
- 默认（无参数）：MCP server（FastMCP，Stdio per-call 短命进程，平台唯一接入路径）。
- serve：本地 UI 服务（uvicorn + FastAPI，仅绑定 127.0.0.1，默认端口 8723，env 可配）。
"""

from __future__ import annotations

import os
import sys


def _serve() -> None:
    """常驻 UI 服务：REST API + 静态托管 React 构建产物。"""
    import uvicorn

    from .api import create_app

    host = os.environ.get("LEDGER_HOST", "127.0.0.1")
    port = int(os.environ.get("LEDGER_PORT", "8723"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def main() -> None:
    # `python -m ledger serve` → UI 常驻；`python -m ledger` → MCP stdio（平台接入形态）。
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        _serve()
        return

    from .mcp_server import build_mcp

    build_mcp().run()


if __name__ == "__main__":
    main()
