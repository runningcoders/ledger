"""数据层：短连接（打开→执行→关闭）+ open_with_retry 撞锁重试 + 首启建库。

口径来源：架构设计方案 v1.0 §2.2（关键设计）：
- 短连接原则：两个入口都不持有常驻 DB 连接；写锁窗口=单次操作毫秒级。
- 撞锁重试：open_with_retry 重试 5 次、退避 0.2s 递增（总窗口约 3s），
  远大于对端写锁窗口；重试耗尽返回明确业务错误（db_busy）。
- 数据文件：每用户独立库 ~/.ai_orz/data/users/{uid}/personal/ledger.duckdb
  （env LEDGER_DB_PATH 显式配置优先；uid 取自结构化 base64 凭证 env LEDGER_USER_ID，
  双入口启动 fail-fast 校验；无旧路径回退分支——多用户拍板①，AMan 01a13e2c）；
  单文件 + WAL；隐私红线：原始明细只落本地文件，不入平台库不入蜂巢图谱。
- 首启建库：_ensure_schema 执行仓库根 schema.sql（IF NOT EXISTS 幂等），
  进程内 Once + threading.Lock 双检（FastAPI sync 路由跑 threadpool，需防并发 init）。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from .errors import LedgerError

DB_LOCK_ATTEMPTS = 5
DB_LOCK_BACKOFF_SECONDS = 0.2

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

_schema_ready = False
_schema_lock = threading.Lock()

# ── 多用户凭证（结构化 base64，AMan 拍板②，01a13e2c）────────────────────────
# 编码链：JSON（UTF-8 紧凑）→ 标准 base64（RFC 4648）→ env LEDGER_USER_ID 单行字符串。
# 不做旧格式嗅探兼容（拍板②-2）：旧格式由下方校验链结构性拒绝。
CREDENTIAL_VERSION = 1
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class CredentialError(Exception):
    """凭证解析失败（fail-fast 语义：入口应立即退出，不降级、不回落默认用户）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_credential(raw: str | None) -> str:
    """解析 LEDGER_USER_ID 原始值，返回 user_id。

    六步校验链（任一步失败抛 CredentialError，不猜不降级，拍板②-3）：
    env 非空 → base64 严格解码（validate=True，字符集外即拒）→ UTF-8 → JSON 对象
    → v==1（缺失/非 int/未知版本拒绝，为演进预留单调递增语义）
    → user_id 必填非空且过白名单 ^[A-Za-z0-9_-]+$
      （uid 用作路径组件，白名单结构性排除 ../、/、. 等路径注入与穿越面）。
    旧格式裸 uid 串：或字符集/padding 不过②，或解码产物非合法 UTF-8 JSON③④——
    校验链结构性拒绝，无需专门嗅探分支（拍板②-2）。
    username 可选本期零消费；未知字段一律忽略（预留拓展位，向前兼容）。
    """
    if not raw:
        raise CredentialError("env_missing", "环境变量 LEDGER_USER_ID 未设置或为空")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialError(
            "base64_invalid", f"LEDGER_USER_ID 不是合法 base64：{exc}"
        ) from exc
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialError("json_invalid", f"凭证解码产物非合法 UTF-8：{exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CredentialError("json_invalid", f"凭证解码产物非合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise CredentialError("json_invalid", "凭证 JSON 顶层须为对象")
    version = payload.get("v")
    # bool 是 int 子类：true/false 语义非版本号，显式排除
    if isinstance(version, bool) or not isinstance(version, int) or version != CREDENTIAL_VERSION:
        raise CredentialError(
            "version_unknown",
            f"凭证版本不支持（要求 v={CREDENTIAL_VERSION}，实际 {version!r}）",
        )
    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise CredentialError("user_id_missing", "凭证缺少 user_id（必填非空字符串）")
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise CredentialError(
            "user_id_invalid", "user_id 含非法字符（白名单 ^[A-Za-z0-9_-]+$）"
        )
    return user_id


_resolved_user_id: str | None = None


def bootstrap_credential() -> str:
    """启动装配：读 env LEDGER_USER_ID → parse_credential → 存模块级缓存供 db_path() 消费。

    双入口（MCP stdio / REST serve）在 main() 最前调用，零豁免（拍板②-3）。
    """
    global _resolved_user_id
    user_id = parse_credential(os.environ.get("LEDGER_USER_ID"))
    _resolved_user_id = user_id
    return user_id


def db_path() -> Path:
    """数据文件路径（两级规则，无旧路径回退分支——多用户拍板①）：

    1. env LEDGER_DB_PATH 显式配置优先（维持既有行为）；
    2. 无显式配置 → 按用户拼 ~/.ai_orz/data/users/{uid}/personal/ledger.duckdb
       （uid 取启动装配缓存；父目录由 _connect_raw() mkdir 自动创建，零新增代码）。

    缓存未装配（未经入口 bootstrap_credential() 直接调 db 层）→ RuntimeError
    （编程错误口径，非运行时降级）。
    """
    env = os.environ.get("LEDGER_DB_PATH")
    if env:
        return Path(env)
    if _resolved_user_id is None:
        raise RuntimeError(
            "用户凭证未装配：请经入口 main() 调用 bootstrap_credential()，"
            "或显式配置 LEDGER_DB_PATH"
        )
    return Path.home() / ".ai_orz" / "data" / "users" / _resolved_user_id / "personal" / "ledger.duckdb"


def _connect_raw() -> duckdb.DuckDBPyConnection:
    """原始连接（含撞锁重试）。DuckDB 单文件读写模式打开即持 OS 级排他锁。"""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(1, DB_LOCK_ATTEMPTS + 1):
        try:
            return duckdb.connect(str(path))
        except duckdb.IOException as exc:  # 撞锁：明确报错而非数据损坏（方案 §1 硬约束）
            msg = str(exc)
            if "lock" not in msg.lower():
                raise
            last_err = exc
            time.sleep(DB_LOCK_BACKOFF_SECONDS * attempt)
    raise LedgerError(
        "db_busy",
        "数据文件忙，请稍候重试（另一入口正在写入）",
        http_status=503,
    ) from last_err


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """首启建库：按仓库根 schema.sql 执行（全部 IF NOT EXISTS / ON CONFLICT 幂等）。"""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name='main'"
            ).fetchall()
        }
        expected = {"categories", "accounts", "transactions", "tags", "transaction_tags", "budgets"}
        if not expected.issubset(existing):
            sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            # 逐语句执行：剥除 -- 注释行后按 ';' 切分（seed 的 INSERT 含分号安全——无存储过程）
            statements = [
                s.strip()
                for s in "\n".join(
                    line for line in sql.splitlines() if not line.strip().startswith("--")
                ).split(";")
                if s.strip()
            ]
            for stmt in statements:
                conn.execute(stmt)
        _schema_ready = True


@contextmanager
def connect() -> Iterator[duckdb.DuckDBPyConnection]:
    """短连接 contextmanager：打开→（首启建库）→交业务执行→关闭。

    用法：`with connect() as conn: conn.execute(...)`——业务层统一入口，
    禁止任何模块绕开本函数自持常驻连接（方案 §2.2 否决项①）。
    """
    conn = _connect_raw()
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()
