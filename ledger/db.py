"""数据层：短连接（打开→执行→关闭）+ open_with_retry 撞锁重试 + 首启建库。

口径来源：架构设计方案 v1.0 §2.2（关键设计）：
- 短连接原则：两个入口都不持有常驻 DB 连接；写锁窗口=单次操作毫秒级。
- 撞锁重试：open_with_retry 重试 5 次、退避 0.2s 递增（总窗口约 3s），
  远大于对端写锁窗口；重试耗尽返回明确业务错误（db_busy）。
- 数据文件：~/.ai_orz/data/personal/ledger.duckdb（env LEDGER_DB_PATH 可配），
  单文件 + WAL；隐私红线：原始明细只落本地文件，不入平台库不入蜂巢图谱。
- 首启建库：_ensure_schema 执行仓库根 schema.sql（IF NOT EXISTS 幂等），
  进程内 Once + threading.Lock 双检（FastAPI sync 路由跑 threadpool，需防并发 init）。
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from .errors import LedgerError

DB_LOCK_ATTEMPTS = 5
DB_LOCK_BACKOFF_SECONDS = 0.2

_DEFAULT_DB_PATH = Path.home() / ".ai_orz" / "data" / "personal" / "ledger.duckdb"
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

_schema_ready = False
_schema_lock = threading.Lock()


def db_path() -> Path:
    """数据文件路径（env LEDGER_DB_PATH 可配；默认 ~/.ai_orz/data/personal/ledger.duckdb）。"""
    env = os.environ.get("LEDGER_DB_PATH")
    return Path(env) if env else _DEFAULT_DB_PATH


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
