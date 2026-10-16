#!/usr/bin/env bash
# 本地 UI 服务（REST 面）：默认绑定 http://127.0.0.1:8723（回环）。
#
# 入口分流（落码单 01a14409，AMan 拍板①）：
#   LEDGER_CREDENTIAL 已设 → 单用户模式（非法凭证 fail-fast exit 2）；
#   LEDGER_CREDENTIAL 未设 → 总管只读模式放行（读聚合全部已注册用户，写需请求级凭证，
#                            顶栏「解锁写入」弹窗粘贴解锁）。
# 端口/地址：LEDGER_HOST（默认 127.0.0.1）/ LEDGER_PORT（默认 8723）env 可配。
# 用法：直接运行本脚本（总管模式）；或 export LEDGER_CREDENTIAL=<凭证串> 后运行（单用户模式）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${LEDGER_CREDENTIAL:-}" ]; then
  echo "==> LEDGER_CREDENTIAL 未设：以总管只读模式启动（读聚合全部用户，写操作在页面解锁）"
else
  echo "==> LEDGER_CREDENTIAL 已设：以单用户模式启动（非法凭证将 fail-fast exit 2）"
fi

HOST="${LEDGER_HOST:-127.0.0.1}"
PORT="${LEDGER_PORT:-8723}"
echo "==> 启动 ledger UI：http://${HOST}:${PORT}（Ctrl+C 退出）"
echo "    首次启动自动建库与 seed（14 分类 + 默认账户「现金」）；库文件位置见 README。"
exec uv run --directory "${REPO_ROOT}" python -m ledger serve
