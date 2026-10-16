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


def list_accounts(uid: str | None = None) -> list[AccountOut]:
    with connect(uid) as conn:
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


def create_account(params: AccountCreate, uid: str | None = None) -> AccountOut:
    with connect(uid) as conn:
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


def update_account(aid: str, params: AccountUpdate, uid: str | None = None) -> AccountOut:
    """可改项克制：名称与启停（销户语义复杂化，P0/P1 无删除动作）。"""
    with connect(uid) as conn:
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


DEFAULT_ACCOUNT_ID = "acc_default_cash"
DEFAULT_ACCOUNT_NAME = "现金"
DEFAULT_ACCOUNT_TYPE = "cash"


def ensure_default_account(conn) -> str:
    """默认账户兜底保障（幂等，账户机制拍板案 B·a）：固定 id/name/type。

    schema.sql 幂等 seed 覆盖新库首启；既有库（seed 引入前建库）由本函数补种。
    若用户已自建同名「现金」账户或已存在固定 id 账户，尊重既有数据不覆盖不并户。
    返回默认账户 id。
    """
    row = conn.execute(
        "SELECT id FROM accounts WHERE name = ? OR id = ?",
        [DEFAULT_ACCOUNT_NAME, DEFAULT_ACCOUNT_ID],
    ).fetchone()
    if row:
        return row[0]
    conn.execute(
        "INSERT INTO accounts (id, name, type, currency, initial_balance)"
        " VALUES (?, ?, ?, 'CNY', 0)"
        " ON CONFLICT DO NOTHING",
        [DEFAULT_ACCOUNT_ID, DEFAULT_ACCOUNT_NAME, DEFAULT_ACCOUNT_TYPE],
    )
    return DEFAULT_ACCOUNT_ID


def resolve_or_create_account_id(name: str) -> str:
    """MCP 写入面 get-or-create（拍板案 B·c 静默建）：按名解析；不存在即自动建档
    （type=virtual 游离开局）；UNIQUE 重名即命中幂等；命中停用账户不复活
    （返回 id 后由服务层统一校验报 account_disabled）。REST 面不经过本函数
    （显式管理动作维持强校验，拍板⑤）。"""
    with connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE name = ?", [name]).fetchone()
        if row:
            return row[0]
        aid = str(uuid.uuid4())
        try:
            conn.execute(
                "INSERT INTO accounts (id, name, type) VALUES (?, ?, 'virtual')",
                [aid, name],
            )
        except duckdb.ConstraintException:
            # 并发重名兜底：命中即幂等（短连接单写者下极难触发，防御性保留）
            row = conn.execute("SELECT id FROM accounts WHERE name = ?", [name]).fetchone()
            if row is None:
                raise
            return row[0]
        return aid


def find_account_id_by_name(name: str) -> Optional[str]:
    """按名查 id（只读不建档）：MCP 查询面筛选专用——不存在返回 None，
    由 M3 短路空集（对齐 stats 空集口径，防查询静默建档产生幽灵账户）。"""
    with connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE name = ?", [name]).fetchone()
    return row[0] if row else None
