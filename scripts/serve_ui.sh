#!/usr/bin/env bash
# 本地 UI 服务（REST 面，单用户模式）：默认绑定 http://127.0.0.1:8723（回环）。
#
# 前置：LEDGER_CREDENTIAL 已构造（scripts/make_credential.sh）——双入口 fail-fast 零豁免。
# 端口/地址：LEDGER_HOST（默认 127.0.0.1）/ LEDGER_PORT（默认 8723）env 可配。
# 用法：export LEDGER_CREDENTIAL=<凭证串> 后运行本脚本；浏览器打开 http://127.0.0.1:8723
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${LEDGER_CREDENTIAL:-}" ]; then
  echo "!! LEDGER_CREDENTIAL 未设置（serve 与 MCP stdio 同样 fail-fast：不降级、不回落默认用户）" >&2
  echo "   构造：LEDGER_USER_ID=<你的UID> scripts/make_credential.sh" >&2
  exit 2
fi

HOST="${LEDGER_HOST:-127.0.0.1}"
PORT="${LEDGER_PORT:-8723}"
echo "==> 启动 ledger UI：http://${HOST}:${PORT}（Ctrl+C 退出）"
echo "    首次启动自动建库与 seed（14 分类 + 默认账户「现金」）；库文件位置见 README。"
exec uv run --directory "${REPO_ROOT}" python -m ledger serve
