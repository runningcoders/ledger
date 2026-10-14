"""services.accounts — 账户 CRUD + 余额派生计算（余额不落冗余列，防口径漂移）。

口径来源：架构设计方案 v1.0 §3「余额口径（防漂移关键设计）」：
当前余额 = initial_balance + Σ支出(负) + Σ收入(正) + Σ转入-转出，派生实时计算。
R4：in_sum=False 仅不计收支统计，资金仍真实变动（信用卡还款类），余额照算。
R5：作废流水不参与余额。转账单行双边处理（R3：只影响余额不影响收支）。
"""

from __future__ import annotations

import uuid
from typing import Optional

import duckdb

from ..db import connect
from ..errors import ConflictError, NotFoundError
from ..models import AccountCreate, AccountOut, AccountUpdate

_BALANCE_SQL = (
    "a.initial_balance + COALESCE(SUM(CASE"
    " WHEN t.id IS NULL THEN 0"
    " WHEN t.status = 'voided' THEN 0"
    " WHEN t.type = 'expense' THEN -t.amount"
    " WHEN t.type = 'income' THEN t.amount"
    " WHEN t.type = 'transfer' AND t.account_id = a.id THEN -t.amount"
    " WHEN t.type = 'transfer' AND t.counter_account_id = a.id THEN t.amount"
    " ELSE 0 END), 0)"
)


def _out(conn, aid: str) -> AccountOut:
    row = conn.execute(
        f"SELECT a.id, a.name, a.type, a.currency, a.initial_balance, a.enabled,"
        f" {_BALANCE_SQL} AS balance"
        " FROM accounts a LEFT JOIN transactions t"
        " ON t.account_id = a.id OR t.counter_account_id = a.id"
        " WHERE a.id = ?"
        " GROUP BY a.id, a.name, a.type, a.currency, a.initial_balance, a.enabled",
        [aid],
    ).fetchone()
    return AccountOut(id=row[0], name=row[1], type=row[2], currency=row[3],
                      initial_balance=row[4], enabled=row[5], balance=row[6])


def list_accounts() -> list[AccountOut]:
    with connect() as conn:
        rows = conn.execute(
            f"SELECT a.id, a.name, a.type, a.currency, a.initial_balance, a.enabled,"
            f" {_BALANCE_SQL} AS balance"
            " FROM accounts a LEFT JOIN transactions t"
            " ON t.account_id = a.id OR t.counter_account_id = a.id"
            " GROUP BY a.id, a.name, a.type, a.currency, a.initial_balance, a.enabled,"
            " a.created_at"
            " ORDER BY a.created_at, a.name"
        ).fetchall()
    return [AccountOut(id=r[0], name=r[1], type=r[2], currency=r[3],
                       initial_balance=r[4], enabled=r[5], balance=r[6]) for r in rows]


def create_account(params: AccountCreate) -> AccountOut:
    with connect() as conn:
        aid = str(uuid.uuid4())
        try:
            conn.execute(
                "INSERT INTO accounts (id, name, type, currency, initial_balance)"
                " VALUES (?, ?, ?, ?, ?)",
                [aid, params.name, params.type, params.currency, params.initial_balance],
            )
        except duckdb.ConstraintException as exc:
            raise ConflictError("account_name_taken", f"账户名已存在：{params.name}") from exc
        return _out(conn, aid)


def update_account(aid: str, params: AccountUpdate) -> AccountOut:
    """可改项克制：名称与启停（销户语义复杂化，P0/P1 无删除动作）。"""
    with connect() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE id = ?", [aid]).fetchone() is None:
            raise NotFoundError("account_not_found", f"账户不存在：{aid}")
        sets, args = [], []
        if params.name is not None:
            sets.append("name = ?")
            args.append(params.name)
        if params.enabled is not None:
            sets.append("enabled = ?")
            args.append(params.enabled)
        if sets:
            args.append(aid)
            try:
                conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", args)
            except duckdb.ConstraintException as exc:
                raise ConflictError("account_name_taken", f"账户名已存在：{params.name}") from exc
        return _out(conn, aid)


def resolve_account_id(name: str) -> str:
    """语义参数解析（MCP 面）：账户名→ID（账户名唯一，单命中）。"""
    with connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE name = ?", [name]).fetchone()
    if row is None:
        raise NotFoundError("account_not_found", f"账户不存在：{name}")
    return row[0]
