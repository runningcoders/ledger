"""services.stats — 区间汇总/分组统计（M5-M7 与 UI 统计共用，MCP 与 REST 同口径）。

口径来源：UI 规范 §3.1/§5.1 + 产品调研 M5：
统计一律排除转账（R3）、不计收支项（R4）、作废流水（R5）；
结余=收入-支出（不是账户余额合计，两口径严格区分）。
"""

from __future__ import annotations

import re
from datetime import datetime

from ..db import connect
from ..errors import LedgerError
from ..models import GroupStat, OverviewOut, StatsOut
from .budget import _month_bounds, _PERIOD_RE
from .transactions import parse_boundary

_BASE_WHERE = ("t.status = 'active' AND t.in_sum = TRUE"
               " AND t.type IN ('income','expense')"
               " AND t.occurred_at >= ? AND t.occurred_at < ?")


def overview(month: str | None = None) -> OverviewOut:
    """本月收支概览（U2）：income/expense/balance 三读数。"""
    month = month or datetime.now().strftime("%Y-%m")
    if not _PERIOD_RE.match(month):
        raise LedgerError("invalid_period", "周期格式须为 YYYY-MM")
    start, end = _month_bounds(month)
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN type = 'income' THEN amount END), 0),"
            " COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0)"
            f" FROM transactions t WHERE {_BASE_WHERE}",
            [start, end],
        ).fetchone()
    income, expense = row
    return OverviewOut(month=month, income=income, expense=expense,
                       balance=income - expense)


def summary(*, start, end, group_by: str = "category",
            direction: str | None = None) -> StatsOut:
    """区间分组统计（M5/U3 数据源）：group_by=category/account/tag/day/month。

    tag 维度：一笔多标签会计入多个分组（横切维度语义，如实呈现）。
    """
    if group_by not in {"category", "account", "tag", "day", "month"}:
        raise LedgerError("invalid_group_by",
                          "group_by 须为 category/account/tag/day/month")
    s = parse_boundary(start)
    e = parse_boundary(end, is_end=True)
    args: list = [s, e]
    where = _BASE_WHERE
    if direction in ("income", "expense"):
        where += " AND t.type = ?"
        args.append(direction)

    if group_by == "category":
        joins = "JOIN categories c ON c.id = t.category_id"
        key_expr, name_expr, group_expr = "c.id", "c.name", "c.id, c.name"
    elif group_by == "account":
        joins = "JOIN accounts a ON a.id = t.account_id"
        key_expr, name_expr, group_expr = "a.id", "a.name", "a.id, a.name"
    elif group_by == "tag":
        joins = ("JOIN transaction_tags tt ON tt.transaction_id = t.id"
                 " JOIN tags tg ON tg.id = tt.tag_id")
        key_expr, name_expr, group_expr = "tg.id", "tg.name", "tg.id, tg.name"
    else:
        fmt = "%Y-%m-%d" if group_by == "day" else "%Y-%m"
        joins = ""
        key_expr = name_expr = group_expr = f"strftime(t.occurred_at, '{fmt}')"

    with connect() as conn:
        rows = conn.execute(
            f"SELECT {key_expr}, {name_expr},"
            " COALESCE(SUM(CASE WHEN t.type = 'income' THEN t.amount END), 0) AS income,"
            " COALESCE(SUM(CASE WHEN t.type = 'expense' THEN t.amount END), 0) AS expense"
            f" FROM transactions t {joins} WHERE {where} GROUP BY {group_expr}",
            args,
        ).fetchall()
    items = [GroupStat(key=k, name=n, income=i, expense=x, net=i - x)
             for k, n, i, x in rows]
    items.sort(key=lambda g: g.expense + g.income, reverse=True)
    return StatsOut(start=str(start), end=str(end), group_by=group_by, items=items)
