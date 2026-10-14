"""数据层测试：首启建库幂等 + 撞锁重试 + 路径解析。"""

from __future__ import annotations

from unittest import mock

import duckdb
import pytest

import ledger.db as db
from ledger.errors import LedgerError

EXPECTED_TABLES = {"categories", "accounts", "transactions", "tags",
                   "transaction_tags", "budgets"}


def test_first_init_creates_all_tables_and_seed():
    with db.connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name='main'").fetchall()}
        assert EXPECTED_TABLES <= tables
        n = conn.execute("SELECT COUNT(*) FROM categories WHERE is_preset").fetchone()[0]
        assert n == 14  # §12.6 拍板：支出 9 + 收入 5


def test_second_connect_idempotent_no_duplicate_seed():
    with db.connect():
        pass
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        assert n == 14
        rows = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE name='其他'").fetchone()[0]
        assert rows == 2  # 支出/收入各一个「其他」


def test_db_path_default_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    assert db.db_path() == db._DEFAULT_DB_PATH
    monkeypatch.setenv("LEDGER_DB_PATH", str(tmp_path / "x.duckdb"))
    assert db.db_path() == tmp_path / "x.duckdb"


def test_lock_retry_succeeds_within_attempts():
    calls = {"n": 0}
    real = duckdb.connect

    def flaky(path):
        calls["n"] += 1
        if calls["n"] <= 2:  # 前两次撞锁（IOException 含 lock）
            raise duckdb.IOException("Could not set lock on file")
        return real(path)

    with mock.patch.object(db.duckdb, "connect", side_effect=flaky), \
         mock.patch.object(db.time, "sleep"):
        with db.connect() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert calls["n"] == 3


def test_lock_exhausted_returns_business_error():
    def busy(_path):
        raise duckdb.IOException("Could not set lock on file: held by another process")

    with mock.patch.object(db.duckdb, "connect", side_effect=busy), \
         mock.patch.object(db.time, "sleep"):
        with pytest.raises(LedgerError) as ei:
            db._connect_raw()
    assert ei.value.code == "db_busy"
    assert ei.value.http_status == 503
