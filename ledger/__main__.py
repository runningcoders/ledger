"""入口分发：`python -m ledger serve` = UI 常驻；默认 = MCP stdio。

口径来源：架构设计方案 v1.0 §2.3/§2.4/§4 + 多用户拍板（AMan 01a13e2c）
+ 总管只读模式评估报告 01a14009 评估面 a（AMan 拍板①按推荐，落码单 01a14409）：
- 默认（无参数）：MCP server（FastMCP，Stdio per-call 短命进程，平台唯一接入路径）。
  启动校验零豁免：凭证（env LEDGER_CREDENTIAL 结构化 base64）解析失败 fail-fast
  —— stderr 一行结构化错误 + sys.exit(2)，不降级、不回落默认用户。
- serve：本地 UI 服务（uvicorn + FastAPI，仅绑定 127.0.0.1，默认端口 8723，env 可配）。
  入口分流（评估面 a）：LEDGER_CREDENTIAL 已设=单用户模式（现行为逐字节不变，
  凭证解析失败同样 fail-fast）；未设=总管只读模式放行——读端点聚合全部已注册
  用户数据（发现锚=personal/ledger.duckdb 文件存在性），写端点要求请求级凭证
  （X-Ledger-Credential 请求头，HTTP 401 语义）。
"""

from __future__ import annotations

import os
import sys

_CREDENTIAL_FIX_HINT = """修复指引：echo '{"v":1,"user_id":"<你的UID>"}' | base64"""


def _serve() -> None:
    """常驻 UI 服务：REST API + 静态托管 React 构建产物。

    模式分流：LEDGER_CREDENTIAL 已设 → bootstrap fail-fast（单用户模式，
    与 MCP 同一口径零豁免）；未设 → 总管只读模式直接放行（REST 读编排/
    写凭证检查在 api 层按模式分派，见 create_app）。
    """
    import uvicorn

    from .api import create_app
    from .db import CredentialError, bootstrap_credential, single_user_mode

    if single_user_mode():
        try:
            bootstrap_credential()
        except CredentialError as exc:
            print(
                f"ledger 启动失败：credential_error code={exc.code} {exc.message}；"
                f"{_CREDENTIAL_FIX_HINT}",
                file=sys.stderr,
            )
            sys.exit(2)

    if os.environ.get("LEDGER_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
        # 安全缓解②（评估面 f，AMan 拍板⑤采认）：非回环绑定警示（一行 stderr，不阻断）
        print(
            "ledger 警告：总管模式聚合暴露全部用户账本，当前 LEDGER_HOST 为非回环地址，"
            "请确认访问面可信（默认应保持 127.0.0.1）",
            file=sys.stderr,
        )

    host = os.environ.get("LEDGER_HOST", "127.0.0.1")
    port = int(os.environ.get("LEDGER_PORT", "8723"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def main() -> None:
    # MCP stdio 分支（平台接入形态）：启动校验零豁免（拍板②-3）——
    # 凭证解析失败 → stderr 一行结构化错误 + exit(2)。
    from .db import CredentialError, bootstrap_credential

    # `python -m ledger serve` → UI 常驻（serve 分流见 _serve）；`python -m ledger` → MCP stdio。
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        _serve()
        return

    try:
        bootstrap_credential()
    except CredentialError as exc:
        print(
            f"ledger 启动失败：credential_error code={exc.code} {exc.message}；"
            f"{_CREDENTIAL_FIX_HINT}",
            file=sys.stderr,
        )
        sys.exit(2)

    from .mcp_server import build_mcp

    build_mcp().run()


if __name__ == "__main__":
    main()
