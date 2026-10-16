"""services.budget — 预算设定与状态计算（R7 三态：正常 / 临近超支≥80% / 超支≥100%）。

口径来源：产品调研报告 R7 + UI 规范 §5.2 + 架构设计方案 §3 E5：
- 周期固定按月（'YYYY-MM'）；总预算（category_id=NULL）+ 分类预算。
- 分类预算 used=本级+直接子级支出（R6 两级封顶，一层递归即完整子树）。
- used 口径=expense 且 in_sum 且 active（R3 排除转账、R4 排除不计收支、R5 排除作废）。
- PUT 语义=整体替换该月预算集（事务内 DELETE+INSERT，原子）。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal

from ..db import connect
from ..errors import ConflictError, LedgerError, NotFoundError
from ..models import BudgetLine, BudgetSetRequest, BudgetStatusOut

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")
_NEAR_LIMIT_RATIO = 0.8  # UI 规范 §7.3 建议值，可在实现时调整


def _check_period(period: str) -> None:
    if not _PERIOD_RE.match(period):
        raise LedgerError("invalid_period", "周期格式须为 YYYY-MM")


def _month_bounds(period: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(period, "%Y-%m")
    y, m = start.year, start.month
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start, end


def set_budgets(req: BudgetSetRequest, uid: str | None = None) -> BudgetStatusOut:
    _check_period(req.period)
    cat_ids = [i.category_id for i in req.items]
    if cat_ids.count(None) > 1:
        raise ConflictError("budget_total_duplicate", "总预算只能设一条")
    seen: set[str] = set()
    for cid in cat_ids:
        if cid is None:
            continue
        if cid in seen:
            raise ConflictError("budget_category_duplicate", "同一分类预算重复")
        seen.add(cid)
    with connect(uid) as conn:
        for cid in seen:
            if conn.execute("SELECT 1 FROM categories WHERE id = ?", [cid]).fetchone() is None:
                raise NotFoundError("category_not_found", f"分类不存在：{cid}")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM budgets WHERE period = ?", [req.period])
            for item in req.items:
                conn.execute(
                    "INSERT INTO budgets (id, period, category_id, amount) VALUES (?, ?, ?, ?)",
                    [str(uuid.uuid4()), req.period, item.category_id, item.amount],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return budget_status(req.period, uid=uid)


def budget_status(period: str | None = None, uid: str | None = None) -> BudgetStatusOut:
    period = period or datetime.now().strftime("%Y-%m")
    _check_period(period)
    start, end = _month_bounds(period)
    lines: list[BudgetLine] = []
    with connect(uid) as conn:
        rows = conn.execute(
            "SELECT b.category_id, b.amount, c.name FROM budgets b"
            " LEFT JOIN categories c ON c.id = b.category_id"
            " WHERE b.period = ?"
            " ORDER BY (b.category_id IS NULL) DESC, c.sort, c.name",
            [period],
        ).fetchall()
        for cid, amount, cname in rows:
            used = _used_in_month(conn, cid, start, end)
            lines.append(_line(cid, cname, amount, used))
    return BudgetStatusOut(period=period, lines=lines)


def _used_in_month(conn, category_id: str | None,
                   start: datetime, end: datetime) -> Decimal:
    base = ("SELECT COALESCE(SUM(t.amount), 0) FROM transactions t WHERE"
            " t.type = 'expense' AND t.in_sum = TRUE AND t.status = 'active'"
            " AND t.occurred_at >= ? AND t.occurred_at < ?")
    args: list = [start, end]
    if category_id is None:
        return conn.execute(base, args).fetchone()[0]  # 总预算：当月全部有效支出
    # 分类预算：本级 + 直接子级（R6 两级封顶）
    return conn.execute(
        base + " AND (t.category_id = ? OR t.category_id IN"
        " (SELECT id FROM categories WHERE parent_id = ?))",
        [*args, category_id, category_id],
    ).fetchone()[0]


def _line(cid, cname, amount, used) -> BudgetLine:
    ratio = float(used / amount) if amount else 0.0
    status = "over" if ratio >= 1 else ("near_limit" if ratio >= _NEAR_LIMIT_RATIO else "normal")
    return BudgetLine(category_id=cid, category_name=cname, amount=amount,
                      used=used, remaining=amount - used, status=status)
