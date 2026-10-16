"""MCP server 薄层（官方 SDK FastMCP）：M1-M7 工具注册 + 语义参数解析。

口径来源：架构设计方案 v1.0 §2.4/§4/§7 + 产品调研报告 §2.1：
- Stdio per-call 短命进程（平台唯一接入路径，mcp.rs 实锚）；每次调用独立拉起用后即关。
- 业务逻辑一律下沉 services/；本文件只做「工具签名 + 语义参数解析 + 调 services + 返回」。
- 工具面 M1-M7（M2 拆 update/void 两动作）；配置管理类（分类/账户增删改）不做进 MCP
  （调研口径：低频配置走 UI 面，防 Agent 工具面膨胀）。
- 语义参数：分类/账户按名传参（get-or-create 标签、名→ID 解析），也接受 ID 直传。
- 账户写入面语义（账户机制拍板案 B·AMan 01a14374，任务 01a14375）：M1/M2 显式账户
  不存在→get-or-create 自动建档（type=virtual，命中停用账户仍由服务层报
  account_disabled 不复活）；M1 account 缺省→默认账户「现金」（schema.sql seed）；
  M3 查询面账户筛选不存在→空集非报错（不静默建档，防幽灵账户污染余额）。
- 错误：LedgerError 冒泡，由 FastMCP 转 isError 语义返回（同一 message 文案源 errors.py）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from .errors import LedgerError
from .services import accounts as accounts_svc
from .services import budget as budget_svc
from .services import categories as categories_svc
from .services import stats as stats_svc
from .services import transactions as txns_svc
from .services.transactions import parse_boundary

TxnTypeArg = Literal["expense", "income", "transfer"]


def _amount(value: str | float | int) -> Decimal:
    """金额入参解析：MCP 侧统一按字符串精确解析（禁 float 承载金额）。"""
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise LedgerError("invalid_amount", f"金额格式不正确：{value}") from exc


def _occurred(value: str | None) -> datetime | None:
    if value is None:
        return None
    return parse_boundary(value)


def _category_ref(ref: str | None, txn_type: str | None = None) -> str | None:
    """分类引用解析：先按 ID 直传，否则按名解析（支出/收入同名时按交易方向消歧）。"""
    if ref is None:
        return None
    try:
        uuid.UUID(str(ref))
        return str(ref)  # ID 直传
    except ValueError:
        return categories_svc.resolve_category_id(
            str(ref), direction=txn_type if txn_type in ("expense", "income") else None)


def _account_ref(ref: str) -> str:
    try:
        uuid.UUID(str(ref))
        return str(ref)
    except ValueError:
        return accounts_svc.resolve_account_id(str(ref))


def _default_account_id() -> str:
    """默认账户兜底解析（M1 缺省账户双保险）：先查 seed（读），未命中补种（写）
    ——schema.sql 幂等 seed 覆盖新库；本函数覆盖既有库补种与防御性兜底。"""
    from .db import connect

    with connect() as conn:
        return accounts_svc.ensure_default_account(conn)


def _account_write_ref(ref: str) -> str:
    """账户写入面引用解析（get-or-create 拍板案 B）：ID 直传，否则按名
    resolve-or-create（自动建档 type=virtual）。"""
    try:
        uuid.UUID(str(ref))
        return str(ref)
    except ValueError:
        return accounts_svc.resolve_or_create_account_id(str(ref))


def _find_query_ref(ref: str) -> str | None:
    """账户查询面引用解析（只读不建档）：ID 直传，否则按名查找，
    不存在返回 None → M3 短路空集（非报错）。"""
    try:
        uuid.UUID(str(ref))
        return str(ref)
    except ValueError:
        return accounts_svc.find_account_id_by_name(str(ref))


def build_mcp() -> FastMCP:
    mcp = FastMCP("ledger")

    # ---------- M1 记一笔 ----------
    @mcp.tool()
    def add_transaction(
        txn_type: TxnTypeArg,
        amount: str,
        account: Optional[str] = None,
        category: Optional[str] = None,
        counter_account: Optional[str] = None,
        occurred_at: Optional[str] = None,
        note: Optional[str] = None,
        in_sum: bool = True,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """记一笔账。txn_type: expense=支出 / income=收入 / transfer=转账。

        amount 为金额字符串（如 "12.50"，必须 >0）；account/counter_account/category
        按名称或 ID 传参；account 缺省时记账到默认账户「现金」（账户机制拍板案 B·a）；
        显式指定不存在的账户将自动建档（type=virtual）；转账（transfer）不挂分类且
        必须提供对方账户，且不计收支；occurred_at 为 ISO 时间字符串，缺省=当下；
        tags 为标签名列表（不存在即创建）。
        """
        from .models import TransactionCreate

        params = TransactionCreate(
            type=txn_type,
            amount=_amount(amount),
            category_id=_category_ref(category, txn_type),
            account_id=(_account_write_ref(account) if account
                        else _default_account_id()),
            counter_account_id=(_account_write_ref(counter_account)
                                if counter_account else None),
            occurred_at=_occurred(occurred_at),
            note=note,
            in_sum=in_sum,
            tags=tags or [],
        )
        return txns_svc.create(params).model_dump(mode="json")

    # ---------- M2 修改 / 作废 ----------
    @mcp.tool()
    def update_transaction(
        txn_id: str,
        txn_type: Optional[TxnTypeArg] = None,
        amount: Optional[str] = None,
        account: Optional[str] = None,
        category: Optional[str] = None,
        counter_account: Optional[str] = None,
        occurred_at: Optional[str] = None,
        note: Optional[str] = None,
        in_sum: Optional[bool] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """按流水 ID 修改一笔账（仅传需要修改的字段，未传字段保持不变）。

        作废流水不可修改；改为转账时自动清空分类并强制不计收支（R3）。
        """
        from .models import TransactionUpdate

        params = TransactionUpdate(
            type=txn_type,
            amount=_amount(amount) if amount is not None else None,
            category_id=_category_ref(category, txn_type) if category is not None else None,
            account_id=(_account_write_ref(account)
                        if account is not None else None),
            counter_account_id=(_account_write_ref(counter_account)
                                if counter_account is not None else None),
            occurred_at=_occurred(occurred_at),
            note=note,
            in_sum=in_sum,
            tags=tags,
        )
        return txns_svc.update(txn_id, params).model_dump(mode="json")

    @mcp.tool()
    def void_transaction(txn_id: str) -> dict:
        """作废一笔账（不物理删除，保留可追溯；重复作废报错）。"""
        return txns_svc.void(txn_id).model_dump(mode="json")

    # ---------- M3/M4 查流水 / 查单笔 ----------
    @mcp.tool()
    def get_transaction(txn_id: str) -> dict:
        """按流水 ID 查询单笔详情。"""
        return txns_svc.get(txn_id).model_dump(mode="json")

    @mcp.tool()
    def list_transactions(
        start: Optional[str] = None,
        end: Optional[str] = None,
        txn_type: Optional[TxnTypeArg] = None,
        category: Optional[str] = None,
        account: Optional[str] = None,
        tag: Optional[str] = None,
        keyword: Optional[str] = None,
        min_amount: Optional[str] = None,
        max_amount: Optional[str] = None,
        include_voided: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查流水（时间倒序分页）。组合筛选：时间段（ISO 或 YYYY-MM-DD）+ 类型 + 分类
        （含子分类）+ 账户（含转账双侧）+ 标签 + 关键词（匹配备注与分类名）+ 金额区间。

        返回 {items: [...], total: N}；默认不含作废流水（include_voided=true 时含）。
        """
        # 查询面账户筛选（账户机制拍板案 B）：账户名不存在→直接空集非报错
        # （对齐 stats 空集口径；只读不建档，防查询静默建档产生幽灵账户污染余额）。
        account_id = _find_query_ref(account) if account else None
        if account and account_id is None:
            return {"items": [], "total": 0}

        return txns_svc.list_transactions(
            start=start, end=end, txn_type=txn_type,
            category_id=_category_ref(category) if category else None,
            account_id=account_id,
            tag=tag, keyword=keyword,
            min_amount=_amount(min_amount) if min_amount else None,
            max_amount=_amount(max_amount) if max_amount else None,
            include_voided=include_voided, limit=limit, offset=offset,
        ).model_dump(mode="json")

    # ---------- M5 收支统计 ----------
    @mcp.tool()
    def summarize(
        start: str,
        end: str,
        group_by: Literal["category", "account", "tag", "day", "month"] = "category",
        direction: Optional[Literal["income", "expense"]] = None,
    ) -> dict:
        """区间收支统计（排除转账与不计收支项）。group_by 决定分组维度，
        返回各组 {key, name, income, expense, net}；direction 可只看收入或支出。"""
        return stats_svc.summary(
            start=start, end=end, group_by=group_by, direction=direction
        ).model_dump(mode="json")

    # ---------- M6 账户余额概览 ----------
    @mcp.tool()
    def account_balances() -> dict:
        """各账户当前余额（派生实时计算，含转账双边与不计收支项的资金变动）。

        返回 {"accounts": [...]}：显式字典包装，保证 MCP structuredContent
        为单一可预测 JSON 文档（裸 list 会被 SDK 拆分包装，契约形态不可控）。
        """
        return {"accounts": [a.model_dump(mode="json")
                             for a in accounts_svc.list_accounts()]}

    # ---------- M7 预算状态 ----------
    @mcp.tool()
    def budget_status(period: Optional[str] = None) -> dict:
        """预算执行状态（缺省=当月）：总预算+各分类预算的 已用/剩余/三态
        （normal 正常 / near_limit 临近超支≥80% / over 超支≥100%）。"""
        return budget_svc.budget_status(period).model_dump(mode="json")

    return mcp
