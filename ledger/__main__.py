"""入口分发：`uv run python -m ledger serve` = UI 常驻；默认 = MCP stdio。

口径来源：架构设计方案 v1.0 §2.3/§2.4/§4 + 多用户拍板（AMan 01a13e2c）。
- 默认（无参数）：MCP server（FastMCP，Stdio per-call 短命进程，平台唯一接入路径）。
- serve：本地 UI 服务（uvicorn + FastAPI，仅绑定 127.0.0.1，默认端口 8723，env 可配）。
- 启动校验：双入口零豁免——凭证（env LEDGER_CREDENTIAL 结构化 base64）解析失败
  fail-fast：stderr 一行结构化错误 + sys.exit(2)，不降级、不回落默认用户。
"""

from __future__ import annotations

import os
import sys

_CREDENTIAL_FIX_HINT = """修复指引：echo '{"v":1,"user_id":"<你的UID>"}' | base64"""


def _serve() -> None:
    """常驻 UI 服务：REST API + 静态托管 React 构建产物。"""
    import uvicorn

    from .api import create_app

    host = os.environ.get("LEDGER_HOST", "127.0.0.1")
    port = int(os.environ.get("LEDGER_PORT", "8723"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def main() -> None:
    # 启动校验（双入口零豁免，拍板②-3）：凭证解析失败 → stderr 一行结构化错误 + exit(2)。
    from .db import CredentialError, bootstrap_credential

    try:
        bootstrap_credential()
    except CredentialError as exc:
        print(
            f"ledger 启动失败：credential_error code={exc.code} {exc.message}；"
            f"{_CREDENTIAL_FIX_HINT}",
            file=sys.stderr,
        )
        sys.exit(2)

    # `python -m ledger serve` → UI 常驻；`python -m ledger` → MCP stdio（平台接入形态）。
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        _serve()
        return

    from .mcp_server import build_mcp

    build_mcp().run()


if __name__ == "__main__":
    main()
