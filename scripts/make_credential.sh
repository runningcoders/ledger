#!/usr/bin/env bash
# 构造 LEDGER_CREDENTIAL（结构化 base64 凭证串；服务端六步校验链口径）。
#
# 用法：LEDGER_USER_ID=<你的UID> [LEDGER_USERNAME=<用户名>] scripts/make_credential.sh
#
# 输出：一行 base64 凭证串（export LEDGER_CREDENTIAL='<整行>' 使用），
#      并用仓库内 parse_credential 实测六步校验链回验（口径与服务端一致）。
#
# 隐私纪律：本脚本不落盘、不把凭证串写入任何文件；UID 由调用方传入；
#          文档示例一律 <你的UID> 占位符，严禁出现真实凭证明文。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${LEDGER_USER_ID:-}" ]; then
  echo "用法：LEDGER_USER_ID=<你的UID> [LEDGER_USERNAME=<用户名>] $0" >&2
  exit 2
fi

if ! printf '%s' "${LEDGER_USER_ID}" | grep -Eq '^[A-Za-z0-9_-]+$'; then
  echo "!! LEDGER_USER_ID 含非法字符（白名单 ^[A-Za-z0-9_-]+$，与服务端 user_id_invalid 口径一致）" >&2
  exit 2
fi

CRED="$(printf '{"v":1,"user_id":"%s","username":"%s"}' \
  "${LEDGER_USER_ID}" "${LEDGER_USERNAME:-}" | base64 | tr -d '\n')"

echo "==> 凭证已构造（base64 单行；等价于用户身份，勿提交仓库/勿入日志/勿明文示例）："
printf '%s\n' "${CRED}"
echo
echo "==> 使用方式：export LEDGER_CREDENTIAL='<上行整行>'"
echo

# 回验：优先用仓库内 parse_credential 实测六步校验链（口径与服务端一致）。
if command -v uv >/dev/null 2>&1; then
  echo "==> 六步校验链回验（parse_credential 实测）："
  VERIFY_RC=0
  LEDGER_CREDENTIAL="${CRED}" uv run --directory "${REPO_ROOT}" python - <<'PYEOF' || VERIFY_RC=$?
import os, sys
from ledger.db import parse_credential, CredentialError
try:
    uid = parse_credential(os.environ["LEDGER_CREDENTIAL"])
    print(f"    通过：user_id = {uid}")
except CredentialError as e:
    print(f"    失败：credential_error code={e.code} {e.message}", file=sys.stderr)
    sys.exit(2)
PYEOF
  if [ "${VERIFY_RC}" -ne 0 ]; then
    echo "!! 回验未通过：请按上方 credential_error 提示修正后重试（若提示依赖缺失，先跑 scripts/setup_env.sh）" >&2
    exit 2
  fi
  echo "==> 完成。下一步：scripts/smoke_mcp_stdio.sh（MCP stdio 握手冒烟）。"
else
  echo "!! 未检测到 uv：跳过六步链回验。安装 uv 后运行 scripts/setup_env.sh，再跑冒烟脚本验证。"
fi
