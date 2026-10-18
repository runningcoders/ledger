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

# 地点分组维度（任务 01a14d06·拍板②修正）：白名单键→loc 列 JSON 键；
# NULL/无 loc 列值统一归「未标注地点」组（COALESCE 包裹，空集亦成组）。
_LOC_GROUP_FIELDS = {
    "loc_city": "city",
    "loc_adcode": "adcode",
    "loc_type": "type",
    "loc_biz": "biz",
}
_LOC_NULL_LABEL = "未标注地点"


def overview(month: str | None = None, uid: str | None = None) -> OverviewOut:
    """本月收支概览（U2）：income/expense/balance 三读数。"""
    month = month or datetime.now().strftime("%Y-%m")
    if not _PERIOD_RE.match(month):
        raise LedgerError("invalid_period", "周期格式须为 YYYY-MM")
    start, end = _month_bounds(month)
    with connect(uid) as conn:
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
            direction: str | None = None,
            category_id: str | None = None,
            account_id: str | None = None,
            tag: list[str] | None = None,
            uid: str | None = None) -> StatsOut:
    """区间分组统计（M5/U3 数据源）：group_by=category/account/tag/day/month。

    tag 维度：一笔多标签会计入多个分组（横切维度语义，如实呈现）。

    地点分组维度（任务 01a14d06·拍板②修正）：loc_city/loc_adcode/loc_type/loc_biz
    按 loc 列 JSON 串 json_extract_string 提取分组；NULL 统一归「未标注地点」组。

    筛选维度（P1 扩展，可选；不传即沿用既有口径，向后兼容）：
    - category_id：筛选含全部子分类（R6 两级封顶：一层子级即完整子树），
      语义与查流水（list_transactions）一致——父级与其子级的流水均纳入；
      分组仍按流水自身分类呈现（父子不折叠，与不过滤时同口径，
      跨级合计由 day/month 维度小计或前端树聚合承载）。
    - account_id：仅命中流水 account_id 字段（收支流水只挂单侧账户；
      转账双侧不进收支口径已被 R3 排除，无需对侧命中）。
    - tag：标签名多值 OR 语义（与查流水一致）；全空串入参等同未传。
    筛选后无匹配项是合法结果（items=[]，空集非错误）。
    """
    if group_by not in ({"category", "account", "tag", "day", "month"}
                        | set(_LOC_GROUP_FIELDS)):
        raise LedgerError("invalid_group_by",
                          "group_by 须为 category/account/tag/day/month"
                          "/loc_city/loc_adcode/loc_type/loc_biz")
    s = parse_boundary(start)
    e = parse_boundary(end, is_end=True)
    args: list = [s, e]
    where = _BASE_WHERE
    if direction in ("income", "expense"):
        where += " AND t.type = ?"
        args.append(direction)

    # ---- 筛选维度（与查流水同构的组合式追加）----
    filters: list[str] = []
    if category_id is not None:
        filters.append("(t.category_id = ? OR t.category_id IN"
                       " (SELECT id FROM categories WHERE parent_id = ?))")
        args.extend([category_id, category_id])
    if account_id is not None:
        filters.append("t.account_id = ?")
        args.append(account_id)
    if tag:
        names = [t.strip() for t in tag if t and t.strip()]
        if names:
            ph = ", ".join("?" for _ in names)
            filters.append("EXISTS (SELECT 1 FROM transaction_tags tt"
                           " JOIN tags tg ON tg.id = tt.tag_id"
                           f" WHERE tt.transaction_id = t.id AND tg.name IN ({ph}))")
            args.extend(names)
    if filters:
        where += " AND " + " AND ".join(filters)

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
    elif group_by in _LOC_GROUP_FIELDS:
        joins = ""  # 地点维度直接从本表 loc 列提取，无联接
        expr = (f"COALESCE(json_extract_string(t.loc, "
                f"'$.{_LOC_GROUP_FIELDS[group_by]}'), '{_LOC_NULL_LABEL}')")
        key_expr = name_expr = group_expr = expr
    else:
        fmt = "%Y-%m-%d" if group_by == "day" else "%Y-%m"
        joins = ""
        key_expr = name_expr = group_expr = f"strftime(t.occurred_at, '{fmt}')"

    with connect(uid) as conn:
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
