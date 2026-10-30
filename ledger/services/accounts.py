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
    """可改项克制：名称与启停（销户语义复杂化，P0/P1 无删除动作）。

    被流水引用账户（transactions.account_id/counter_account_id 外键）的父表
    UPDATE 会触发 DuckDB 外键检查（同族盲区，评估单 01a14ea3 定谳；enabled
    停用同理），故采用「桥接暂离」编排：引用流水先改挂桥接临时账户、流水
    标签联接先存后回（流水侧 UPDATE 的父检查对 transaction_tags 尚存子行
    同样敏感，与 transactions.update()/rename_tag 先例同构）、父行原地变更
    后切回并拆除桥接。全程逐语句自动提交——显式事务内先清后改仍触发 FK
    违规（父行 FK 检查不读同事务内未提交的删除）。
    """
    with connect(uid) as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE id = ?", [aid]).fetchone() is None:
            raise NotFoundError("account_not_found", f"账户不存在：{aid}")
        sets, args = [], []
        if params.name is not None:
            # 重名检查前置：冲突零扰动返回（替代原 ConstraintException 兜底
            # ——该兜底会把编排中的 FK 错误误报为「账户名已存在」）
            row = conn.execute(
                "SELECT 1 FROM accounts WHERE name = ? AND id <> ?", [params.name, aid]
            ).fetchone()
            if row is not None:
                raise ConflictError("account_name_taken", f"账户名已存在：{params.name}")
            sets.append("name = ?")
            args.append(params.name)
        if params.enabled is not None:
            sets.append("enabled = ?")
            args.append(params.enabled)
        if sets:
            # 桥接暂离编排：保存+清空引用流水的标签联接 → INSERT 桥接临时
            # 账户（uuid 形态防撞 UNIQUE 名）→ 引用流水改挂桥接 → 父行原地
            # 变更 → 切回本账户 → 删除桥接 → 回写标签联接。中途失败存在联接
            # 丢失窗口——与 void()/rename_tag 先清后写编排固有风险形态一致，
            # 前置检查做足后编排内失败源趋零。
            saved = conn.execute(
                "SELECT transaction_id, tag_id FROM transaction_tags"
                " WHERE transaction_id IN (SELECT id FROM transactions"
                " WHERE account_id = ? OR counter_account_id = ?)"
                " ORDER BY transaction_id, tag_id",
                [aid, aid],
            ).fetchall()
            conn.execute(
                "DELETE FROM transaction_tags WHERE transaction_id IN"
                " (SELECT id FROM transactions WHERE account_id = ?"
                " OR counter_account_id = ?)",
                [aid, aid],
            )
            bridge_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO accounts (id, name, type) VALUES (?, ?, 'virtual')",
                [bridge_id, f"__bridge_{uuid.uuid4().hex}"],
            )
            conn.execute(
                "UPDATE transactions SET account_id = ? WHERE account_id = ?",
                [bridge_id, aid],
            )
            conn.execute(
                "UPDATE transactions SET counter_account_id = ?"
                " WHERE counter_account_id = ?",
                [bridge_id, aid],
            )
            args.append(aid)
            conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", args)
            conn.execute(
                "UPDATE transactions SET account_id = ? WHERE account_id = ?",
                [aid, bridge_id],
            )
            conn.execute(
                "UPDATE transactions SET counter_account_id = ?"
                " WHERE counter_account_id = ?",
                [aid, bridge_id],
            )
            conn.execute("DELETE FROM accounts WHERE id = ?", [bridge_id])
            for t_id, tag_id in saved:
                conn.execute(
                    "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    [t_id, tag_id],
                )
        return _out(conn, aid)


def _is_uuid_form(ref: str) -> bool:
    """ID 形态判别（**只用于「未命中时是否允许自动建档」的决策**）。

    不作为「ID vs 名称」的分流判据（缺陷修复批次 01a18c71，同族
    categories._is_uuid_form 口径）：seed 命名空间主键 acc_default_cash 不是
    UUID 形态却是合法主键——用语法形态判分流＝把合法主键当名称、进而自动建档
    出幽灵虚拟账户。
    """
    try:
        uuid.UUID(str(ref))
        return True
    except ValueError:
        return False


def resolve_account_id(ref: str) -> str:
    """账户引用解析：**先按主键精确查，未命中再按名称查**（账户名唯一，单命中）。

    判别顺序（缺陷修复批次 01a18c71）：原实现只按 name 查，把 seed 主键字面量
    acc_default_cash 当成账户名称（写路径会因此自动建档幽灵虚拟账户；本仓
    live 库尚未发生，属潜在缺陷同期修正）。
    """
    with connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id = ?", [ref]).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM accounts WHERE name = ?", [ref]).fetchone()
    if row is None:
        raise NotFoundError("account_not_found", f"账户不存在：{ref}")
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


def resolve_or_create_account_id(ref: str) -> str:
    """MCP 写入面 get-or-create（拍板案 B·c 静默建）。

    **判别顺序（缺陷修复批次 01a18c71）**：先按主键精确查 → 未命中再按名称 →
    双未命中才进入建档决策：
    - ID 形态（UUID 字面量）→ 严格抛 account_not_found，不建档（原样透传语义
      由写路径 account_not_found 承担，错误码与文案一致，零回归）；
    - 名称形态 → 自动建档（type=virtual 游离开局）。

    命中即返回（含停用账户，不复活；由服务层统一校验报 account_disabled）。
    UNIQUE 重名即命中幂等。REST 面不经过本函数（显式管理动作维持强校验，拍板⑤）。
    """
    ref = str(ref)
    try:
        return resolve_account_id(ref)
    except NotFoundError:
        if _is_uuid_form(ref):
            raise
        with connect() as conn:
            aid = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO accounts (id, name, type) VALUES (?, ?, 'virtual')",
                    [aid, ref],
                )
            except duckdb.ConstraintException:
                # 并发重名兜底：命中即幂等（短连接单写者下极难触发，防御性保留）
                row = conn.execute(
                    "SELECT id FROM accounts WHERE name = ?", [ref]).fetchone()
                if row is None:
                    raise
                return row[0]
            return aid


def find_account_id(ref: str) -> Optional[str]:
    """引用解析（只读不建档）：**先按主键精确查，未命中再按名称查**；都不存在
    返回 None。MCP 查询面筛选专用——不存在即短路空集（对齐 stats 空集口径，
    防查询静默建档产生幽灵账户）。

    判别顺序同 resolve_account_id（缺陷修复批次 01a18c71）：查询面传 seed 主键
    acc_default_cash 也应命中真实账户，而不是因「非 UUID 形态」被当名称查空。
    """
    with connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id = ?", [ref]).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM accounts WHERE name = ?", [ref]).fetchone()
    return row[0] if row else None


def find_account_id_by_name(name: str) -> Optional[str]:
    """按名查 id（只读不建档，历史入口保留）：不存在返回 None。

    新代码请用 find_account_id（id 优先）；本函数语义严格限定「按名」，
    供既有调用面零回归。
    """
    with connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE name = ?", [name]).fetchone()
    return row[0] if row else None
