#!/usr/bin/env bash
# 环境准备与依赖安装（上手第一步）：检查 uv → uv sync → 依赖版本自检。
# 用法：在仓库根目录执行 scripts/setup_env.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> 仓库根：${REPO_ROOT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "!! 未检测到 uv。安装：curl -LsSf https://astral.sh/uv/install.sh | sh（或 brew install uv）" >&2
  exit 1
fi
echo "==> uv：$(uv --version)"

echo "==> uv sync（安装/同步依赖，含 dev 测试组）..."
uv sync --directory "${REPO_ROOT}"

echo "==> 依赖自检："
uv run --directory "${REPO_ROOT}" python -c \
  "import mcp, fastapi, duckdb, pydantic; print('    mcp / fastapi / duckdb / pydantic import OK')"

echo
echo "==> 完成。下一步：LEDGER_USER_ID=<你的UID> scripts/make_credential.sh 构造凭证。"
