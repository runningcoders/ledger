#!/usr/bin/env bash
# MCP stdio 启动冒烟：验证 LEDGER_CREDENTIAL 过六步校验链 + MCP 握手 + 工具清单。
#
# 行为：
#   - 恒用临时库（mktemp 目录 + LEDGER_DB_PATH，脚本退出即清理）：即便凭证真实，
#     也绝不触碰真实账本（冒烟只做 initialize + tools/list，零业务写入）。
#   - env 已有 LEDGER_CREDENTIAL → 直接用；未设 → 自动构造脱敏冒烟凭证（SMOKEUSER 占位）。
#   - 期望：initialize 返回 serverInfo，tools/list 返回恰 8 个工具。
#
# 用法：[export LEDGER_CREDENTIAL=<凭证串>] scripts/smoke_mcp_stdio.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "!! 未检测到 uv。安装：curl -LsSf https://astral.sh/uv/install.sh | sh（或 brew install uv）" >&2
  exit 1
fi

SMOKE_DIR="$(mktemp -d -t ledger_smoke_XXXXXX)"
trap 'rm -rf "${SMOKE_DIR}"' EXIT

CRED="${LEDGER_CREDENTIAL:-}"
if [ -n "${CRED}" ]; then
  echo "==> 使用当前 env 中的 LEDGER_CREDENTIAL（临时库，零触碰真实账本）"
else
  CRED="$(printf '{"v":1,"user_id":"SMOKEUSER0000000000000001","username":"smoke"}' | base64 | tr -d '\n')"
  echo "==> 未检测到 LEDGER_CREDENTIAL：使用脱敏冒烟凭证（SMOKEUSER 占位，临时库）"
fi

echo "==> 冒烟启动（initialize + tools/list，经 stdio）..."
REQS="${SMOKE_DIR}/requests.jsonl"
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"ledger-smoke","version":"0.0.0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' > "${REQS}"

# cat 后 sleep 1 再关管道：给 server 留足逐行处理与 flush 时间（防 EOF 竞态丢尾响应）。
OUT="$( { cat "${REQS}"; sleep 1; } \
  | LEDGER_CREDENTIAL="${CRED}" LEDGER_DB_PATH="${SMOKE_DIR}/ledger.duckdb" \
    uv run --directory "${REPO_ROOT}" python -m ledger )"

if ! printf '%s' "${OUT}" | grep -q '"serverInfo"'; then
  echo "!! initialize 握手失败，原始输出如下：" >&2
  printf '%s\n' "${OUT}" >&2
  exit 1
fi

EXPECTED_TOOLS="add_transaction update_transaction void_transaction get_transaction list_transactions summarize account_balances budget_status"
MISSING=""
FOUND=0
for t in ${EXPECTED_TOOLS}; do
  if printf '%s' "${OUT}" | grep -q "\"name\":\"${t}\""; then
    FOUND=$((FOUND + 1))
  else
    MISSING="${MISSING} ${t}"
  fi
done

if [ "${FOUND}" -eq 8 ] && [ -z "${MISSING}" ]; then
  echo "==> MCP_STDIO_SMOKE_OK：握手成功 + 工具清单恰 8/8（临时库已清理）。"
else
  echo "!! 工具清单不齐（${FOUND}/8，缺失：${MISSING}），原始输出如下：" >&2
  printf '%s\n' "${OUT}" >&2
  exit 1
fi
