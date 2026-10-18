"""services.transactions — 记账/改账/作废/查询（R1/R3/R4/R5 唯一口径落位）。

口径来源：产品调研报告 v1.0 §3.3 规则 + 架构设计方案 v1.0 §3/§4。
- R1：金额>0（pydantic）、类型/分类/账户必填、时间缺省=当下。
- R3：转账不挂分类、不计收支（服务层强制 + schema CHECK 双保险）；只影响两侧账户余额。
- R4：in_sum=False 仅影响收支统计口径，仍影响账户余额（资金腾挪类）。
- R5：作废=status 翻转，禁物理删除；作废流水不可修改（可追溯兜底）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from ..db import connect
from ..errors import ConflictError, LedgerError, NotFoundError
from ..models import (
    LocationInfo,
    TagOut,
    TransactionCreate,
    TransactionListOut,
    TransactionOut,
    TransactionUpdate,
)

_TXN_COLS = (
    "id, type, amount, currency, category_id, account_id, counter_account_id, "
    "occurred_at, note, in_sum, status, created_at, updated_at, loc"
)
_COLS = [
    "id", "type", "amount", "currency", "category_id", "account_id",
    "counter_account_id", "occurred_at", "note", "in_sum", "status",
    "created_at", "updated_at", "loc",
]


def _naive(dt: datetime) -> datetime:
    """DuckDB TIMESTAMP 为 naive；带时区输入按本地时区归一。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def parse_boundary(value, *, is_end: bool = False) -> datetime:
    """时间边界解析：接受 datetime 或 ISO 字符串；'YYYY-MM-DD' 端点按整日取界。

    口径：日期区间=本地 naive 时间 [start 00:00, end+1day 00:00)（is_end=True 时含当日全天）。
    """
    if isinstance(value, datetime):
        return _naive(value)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise LedgerError("invalid_time", f"时间格式不正确：{value}") from exc
    if len(s) == 10 and is_end:
        dt += timedelta(days=1)
    return _naive(dt)


def _tags_of(conn, txid: str) -> list[TagOut]:
    rows = conn.execute(
        "SELECT t.id, t.name FROM tags t "
        "JOIN transaction_tags tt ON tt.tag_id = t.id "
        "WHERE tt.transaction_id = ? ORDER BY t.name",
        [txid],
    ).fetchall()
    return [TagOut(id=r[0], name=r[1]) for r in rows]


def _loc_from_raw(raw: str | None) -> LocationInfo | None:
    """loc 列 JSON 串 → LocationInfo（防御回退：坏 JSON→None 不阻断主流程）。"""
    if not raw:
        return None
    try:
        return LocationInfo.model_validate_json(raw)
    except ValueError:  # 含 pydantic ValidationError
        return None


def _to_out(conn, row) -> TransactionOut:
    data = dict(zip(_COLS, row))
    data["tags"] = _tags_of(conn, data["id"])
    data["location"] = _loc_from_raw(data.pop("loc"))
    return TransactionOut(**data)


def _validate(conn, *, txn_type: str, category_id: Optional[str],
              account_id: str, counter_account_id: Optional[str],
              check_enabled: bool = False) -> None:
    """R1/R3 终态校验（create 全量、update 合并终态后复用同一口径）。"""
    if txn_type == "transfer":
        if not counter_account_id:
            raise LedgerError("transfer_counter_required", "转账必须指定对方账户")
        if counter_account_id == account_id:
            raise LedgerError("transfer_same_account", "转出与转入账户不能相同")
        if category_id is not None:
            raise LedgerError("transfer_category_forbidden", "转账不挂分类（转账不计收支）")
    else:
        if not category_id:
            raise LedgerError("category_required", "记账必须指定分类")
        row = conn.execute(
            "SELECT direction, enabled FROM categories WHERE id = ?", [category_id]
        ).fetchone()
        if row is None:
            raise NotFoundError("category_not_found", f"分类不存在：{category_id}")
        if check_enabled and not row[1]:
            raise LedgerError("category_disabled", "分类已停用")
        if row[0] != txn_type:
            raise LedgerError("category_direction_mismatch", "分类方向与交易类型不匹配")
        if counter_account_id is not None:
            raise LedgerError("counter_only_for_transfer", "仅转账可指定对方账户")
    for aid in [account_id] + ([counter_account_id] if counter_account_id else []):
        row = conn.execute("SELECT enabled FROM accounts WHERE id = ?", [aid]).fetchone()
        if row is None:
            raise NotFoundError("account_not_found", f"账户不存在：{aid}")
        if check_enabled and not row[0]:
            raise LedgerError("account_disabled", "账户已停用")


def _tag_ids(conn, names: Iterable[str]) -> list[str]:
    """标签 get-or-create（输入即建，UI 规范 §3.3 口径）。"""
    ids: list[str] = []
    for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
        row = conn.execute("SELECT id FROM tags WHERE name = ?", [name]).fetchone()
        if row:
            ids.append(row[0])
        else:
            tid = str(uuid.uuid4())
            conn.execute("INSERT INTO tags (id, name) VALUES (?, ?)", [tid, name])
            ids.append(tid)
    return ids


def create(params: TransactionCreate, uid: str | None = None) -> TransactionOut:
    with connect(uid) as conn:
        _validate(conn, txn_type=params.type, category_id=params.category_id,
                  account_id=params.account_id,
                  counter_account_id=params.counter_account_id, check_enabled=True)
        txid = str(uuid.uuid4())
        occurred = _naive(params.occurred_at) if params.occurred_at else datetime.now()
        in_sum = False if params.type == "transfer" else params.in_sum  # R3 双保险
        loc_json = (params.location.model_dump_json()
                    if params.location is not None else None)
        conn.execute(
            "INSERT INTO transactions (id, type, amount, category_id, account_id,"
            " counter_account_id, occurred_at, note, in_sum, loc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [txid, params.type, params.amount, params.category_id, params.account_id,
             params.counter_account_id, occurred, params.note, in_sum, loc_json],
        )
        if params.tags:
            for tid in _tag_ids(conn, params.tags):
                conn.execute(
                    "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    [txid, tid],
                )
        row = conn.execute(
            f"SELECT {_TXN_COLS} FROM transactions WHERE id = ?", [txid]
        ).fetchone()
        return _to_out(conn, row)


def get(txid: str, uid: str | None = None) -> TransactionOut:
    with connect(uid) as conn:
        row = conn.execute(
            f"SELECT {_TXN_COLS} FROM transactions WHERE id = ?", [txid]
        ).fetchone()
        if row is None:
            raise NotFoundError("transaction_not_found", f"流水不存在：{txid}")
        return _to_out(conn, row)


def update(txid: str, params: TransactionUpdate, uid: str | None = None) -> TransactionOut:
    """修改=直接覆盖更新（UI 规范 §3.4 口径）；合并终态后按 R1-R4 重校验。"""
    with connect(uid) as conn:
        row = conn.execute(
            f"SELECT {_TXN_COLS} FROM transactions WHERE id = ?", [txid]
        ).fetchone()
        if row is None:
            raise NotFoundError("transaction_not_found", f"流水不存在：{txid}")
        cur = dict(zip(_COLS, row))
        if cur["status"] == "voided":
            raise ConflictError(
                "transaction_voided_immutable", "作废流水不可修改（作废可追溯兜底）")

        merged_type = params.type if params.type is not None else cur["type"]
        merged_account = params.account_id if params.account_id is not None else cur["account_id"]
        merged_counter = (params.counter_account_id if params.counter_account_id is not None
                          else cur["counter_account_id"])
        merged_category = (params.category_id if params.category_id is not None
                           else cur["category_id"])
        merged_in_sum = params.in_sum if params.in_sum is not None else cur["in_sum"]
        if merged_type == "transfer":  # R3：切换为转账时清分类、强制不计收支
            merged_category = None
            merged_in_sum = False
        else:  # 切离转账：对方账户随之清除（仅转账承载对方账户语义，对称规则）
            merged_counter = None
        _validate(conn, txn_type=merged_type, category_id=merged_category,
                  account_id=merged_account, counter_account_id=merged_counter,
                  check_enabled=False)

        # 方案 A「无条件先清+按需回写」：DuckDB 父表 UPDATE 的 FK 检查对尚存
        # 子行敏感，且与是否传 tags 无关（tags=None 局部更新同触发，评估单
        # 01a14ea3 定谳）。未传 tags（局部更新最常见形态）先保存现标签集、
        # UPDATE 后原样回写（净语义=标签保持不变）；传 tags=整体替换语义零变化。
        restore_ids: list[str] = []
        if params.tags is None:
            restore_ids = [r[0] for r in conn.execute(
                "SELECT tag_id FROM transaction_tags WHERE transaction_id = ?"
                " ORDER BY tag_id", [txid]).fetchall()]
        conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", [txid])
        merged_loc_json = (params.location.model_dump_json()
                           if params.location is not None else cur["loc"])
        conn.execute(
            "UPDATE transactions SET type=?, amount=?, category_id=?, account_id=?,"
            " counter_account_id=?, occurred_at=?, note=?, in_sum=?, loc=?,"
            " updated_at=current_timestamp WHERE id=?",
            [merged_type,
             params.amount if params.amount is not None else cur["amount"],
             merged_category, merged_account, merged_counter,
             _naive(params.occurred_at) if params.occurred_at is not None else cur["occurred_at"],
             params.note if params.note is not None else cur["note"],
             merged_in_sum, merged_loc_json, txid],
        )
        if params.tags is not None:
            write_ids = _tag_ids(conn, params.tags)  # 整体替换语义（输入即建）
        else:
            write_ids = restore_ids  # 未传 tags：原样回写
        for tid in write_ids:
            conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                [txid, tid],
            )
        row = conn.execute(
            f"SELECT {_TXN_COLS} FROM transactions WHERE id = ?", [txid]
        ).fetchone()
        return _to_out(conn, row)


def void(txid: str, uid: str | None = None) -> TransactionOut:
    """作废（R5）：status 翻转，不物理删除；重复作废报 409。

    DuckDB 父表 UPDATE 的 FK 检查对尚存子行敏感（同 update() 先例：子表清理
    必须先于父表 UPDATE），故先清标签联接、翻转状态后原样回写同批标签——
    作废流水的标签可追溯性保持（R5 兜底语义）。逐语句自动提交形态与 update()
    先例一致：显式事务内先删后改仍触发 FK 违规（父行 FK 检查不读同事务内
    未提交的删除），故不另起显式事务。
    """
    with connect(uid) as conn:
        row = conn.execute(
            "SELECT status FROM transactions WHERE id = ?", [txid]).fetchone()
        if row is None:
            raise NotFoundError("transaction_not_found", f"流水不存在：{txid}")
        if row[0] == "voided":
            raise ConflictError("transaction_already_voided", "该流水已作废")
        saved = [r[0] for r in conn.execute(
            "SELECT tag_id FROM transaction_tags WHERE transaction_id = ?"
            " ORDER BY tag_id", [txid]).fetchall()]
        conn.execute(
            "DELETE FROM transaction_tags WHERE transaction_id = ?", [txid])
        conn.execute(
            "UPDATE transactions SET status = 'voided',"
            " updated_at = current_timestamp WHERE id = ?", [txid],
        )
        for tid in saved:
            conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id)"
                " VALUES (?, ?)", [txid, tid],
            )
        row = conn.execute(
            f"SELECT {_TXN_COLS} FROM transactions WHERE id = ?", [txid]
        ).fetchone()
        return _to_out(conn, row)

def list_transactions(*, start=None, end=None, txn_type: Optional[str] = None,
                      category_id: Optional[str] = None, account_id: Optional[str] = None,
                      tag=None, keyword: Optional[str] = None,
                      min_amount=None, max_amount=None,
                      loc_city: Optional[str] = None, loc_type: Optional[str] = None,
                      loc_biz: Optional[str] = None, loc_poi_id: Optional[str] = None,
                      loc_keyword: Optional[str] = None,
                      include_voided: bool = False,
                      limit: int = 50, offset: int = 0,
                      uid: str | None = None) -> TransactionListOut:
    """查流水（M3）：组合筛选 + 时间倒序分页；category 含子分类、account 含转账双侧、
    tag 多值 OR 语义、keyword 匹配备注+分类名。

    地点过滤（任务 01a14d06·拍板②修正）：loc_city/loc_type/loc_biz/loc_poi_id
    对 loc 列 JSON 串对应键精确匹配；loc_keyword 对 $.name/$.addr ILIKE 模糊。
    未标注地点（loc NULL/键缺失）不命中任何地点过滤条件（精确过滤语义）。
    """
    where: list[str] = []
    args: list = []
    if start is not None:
        where.append("t.occurred_at >= ?")
        args.append(parse_boundary(start))
    if end is not None:
        where.append("t.occurred_at < ?")
        args.append(parse_boundary(end, is_end=True))
    if txn_type is not None:
        where.append("t.type = ?")
        args.append(txn_type)
    if category_id is not None:
        # 含子分类（R6 两级封顶：一层子级即完整子树）
        where.append("(t.category_id = ? OR t.category_id IN"
                     " (SELECT id FROM categories WHERE parent_id = ?))")
        args.extend([category_id, category_id])
    if account_id is not None:
        where.append("(t.account_id = ? OR t.counter_account_id = ?)")  # 转账双侧
        args.extend([account_id, account_id])
    if tag:
        names = [t.strip() for t in (tag if isinstance(tag, (list, tuple)) else [tag])
                 if t and t.strip()]
        if names:
            ph = ", ".join("?" for _ in names)
            where.append("EXISTS (SELECT 1 FROM transaction_tags tt"
                         " JOIN tags tg ON tg.id = tt.tag_id"
                         f" WHERE tt.transaction_id = t.id AND tg.name IN ({ph}))")
            args.extend(names)
    if keyword:
        kw = f"%{keyword}%"
        where.append("(t.note ILIKE ? OR t.category_id IN"
                     " (SELECT id FROM categories WHERE name ILIKE ?))")
        args.extend([kw, kw])
    for label, raw, op in (("min_amount", min_amount, ">="),
                           ("max_amount", max_amount, "<=")):
        if raw is not None:
            try:
                bound = Decimal(str(raw))
            except InvalidOperation as exc:
                raise LedgerError("invalid_amount", f"金额格式不正确：{raw}") from exc
            where.append(f"t.amount {op} ?")
            args.append(bound)
    for field, value in (("city", loc_city), ("type", loc_type),
                         ("biz", loc_biz), ("poi_id", loc_poi_id)):
        if value is not None:
            where.append(f"json_extract_string(t.loc, '$.{field}') = ?")
            args.append(value)
    if loc_keyword:
        kw = f"%{loc_keyword}%"
        where.append("(json_extract_string(t.loc, '$.name') ILIKE ?"
                     " OR json_extract_string(t.loc, '$.addr') ILIKE ?)")
        args.extend([kw, kw])
    # 地点过滤=业务查询=只看有效笔（任务 01a14fbd·AMan 拍板增补修复项）：
    # loc_* 五参任一生效时无条件强制排除作废笔（无论 include_voided 取值，
    # 软删除不可见于业务过滤）；include_voided 仅在未使用地点过滤时维持
    # 既有语义（显式状态过滤通道不变）。
    has_loc_filter = any(v is not None for v in (
        loc_city, loc_type, loc_biz, loc_poi_id, loc_keyword))
    if not include_voided or has_loc_filter:
        where.append("t.status = 'active'")

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    with connect(uid) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM transactions t{sql_where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_TXN_COLS} FROM transactions t{sql_where}"
            " ORDER BY t.occurred_at DESC, t.created_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        items = [_to_out(conn, r) for r in rows]
    return TransactionListOut(items=items, total=total)
