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

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from .errors import LedgerError
from .models import LocationInfo
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
    """分类引用解析（唯一入口，**本层不做任何形态分流**）：全部下沉到
    services.categories.resolve_or_create_category_id——该函数先按主键精确查
    （seed 命名空间 cat_exp_01/cat_inc_01 等非 UUID 字面量亦是合法主键）、未命中
    再按名称、双未命中才按「是否 UUID 形态 + 方向是否可判定」决定自动建档或
    严格报错。方向不可判定（txn_type=None 局部更新/查询面）与 transfer 不建档
    （既有语义零回归）。

    缺陷修复批次 01a18c71：原实现在本层用 uuid.UUID() 判别「ID vs 名称」，
    非 UUID 的合法主键被误当名称 → 写路径自动建档出同名垃圾分类（实锚：污染
    分类 6bdc4552 / name='cat_inc_01'）。判别职责不由语法形态承担，故本层
    不再持有形态分流——修在服务层，MCP 面与未来任何调用面同时受益。
    """
    if ref is None:
        return None
    return categories_svc.resolve_or_create_category_id(str(ref), txn_type)


def _account_ref(ref: str) -> str:
    """账户引用解析（读）：无形态分流，服务层先 id 精确查、未命中再按名。"""
    return accounts_svc.resolve_account_id(str(ref))


def _default_account_id() -> str:
    """默认账户兜底解析（M1 缺省账户双保险）：先查 seed（读），未命中补种（写）
    ——schema.sql 幂等 seed 覆盖新库；本函数覆盖既有库补种与防御性兜底。"""
    from .db import connect

    with connect() as conn:
        return accounts_svc.ensure_default_account(conn)


def _account_write_ref(ref: str) -> str:
    """账户写入面引用解析（get-or-create 拍板案 B）：无形态分流——服务层先 id
    精确查（seed 主键 acc_default_cash 由此命中，不再建幽灵虚拟账户）、未命中
    再按名；双未命中且 ID 形态严格报错、名称形态自动建档（type=virtual）。"""
    return accounts_svc.resolve_or_create_account_id(str(ref))


def _find_query_ref(ref: str) -> str | None:
    """账户查询面引用解析（只读不建档）：先 id 精确查、未命中再按名，
    都不存在返回 None → M3 短路空集（非报错）。"""
    return accounts_svc.find_account_id(str(ref))


def _received(**fields) -> dict:
    """回执入参快照（对账用，缺陷修复批次 01a18c71）：把服务端**实际收到**的
    关键参数原值回显给调用方，用于当场切分责任面——

    - 回执有值而落库不生效 → 服务端解析/落库缺陷（本次修复范围）；
    - 回执为 null 而模型声称已传 → 平台转发层或模型侧缺失（服务端无法自证，
      正是此前「传了但没生效」无从定谳的缺口）。

    向后兼容：只在原有返回字典上新增 `_received` 键，既有字段与类型零变更。
    """
    return {"_received": fields}


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
        location: Optional[LocationInfo] = None,
    ) -> dict:
        """记一笔账。txn_type: expense=支出 / income=收入 / transfer=转账。

        amount 为金额字符串（如 "12.50"，必须 >0）；account/counter_account/category
        按名称或 ID 传参；account 缺省时记账到默认账户「现金」（账户机制拍板案 B·a）；
        显式指定不存在的账户将自动建档（type=virtual）；转账（transfer）不挂分类且
        必须提供对方账户，且不计收支；occurred_at 为 ISO 时间字符串，缺省=当下；
        tags 为标签名列表（不存在即创建）；location 为地点结构体
        （9 键全可选：name/city/point/poi_id/adcode/type/biz/addr/raw，
        point="经度,纬度" GCJ-02 原值直存直出；整体缺省=无地点）。
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
            location=location,
        )
        out = txns_svc.create(params).model_dump(mode="json")
        out.update(_received(
            occurred_at=occurred_at,
            location=(location.model_dump(mode="json") if location is not None else None),
            tags=tags, account=account, category=category,
            counter_account=counter_account))
        return out

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
        location: Optional[LocationInfo] = None,
    ) -> dict:
        """按流水 ID 修改一笔账（仅传需要修改的字段，未传字段保持不变）。

        作废流水不可修改；改为转账时自动清空分类并强制不计收支（R3）；
        location 结构体（同 add_transaction 9 键语义）：缺省/None=不变更
        （显式清空地点本期不做）。
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
            location=location,
        )
        out = txns_svc.update(txn_id, params).model_dump(mode="json")
        out.update(_received(
            occurred_at=occurred_at,
            location=(location.model_dump(mode="json") if location is not None else None),
            tags=tags, account=account, category=category,
            counter_account=counter_account))
        return out

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
        loc_city: Optional[str] = None,
        loc_type: Optional[str] = None,
        loc_biz: Optional[str] = None,
        loc_poi_id: Optional[str] = None,
        loc_keyword: Optional[str] = None,
        include_voided: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查流水（时间倒序分页）。组合筛选：时间段（ISO 或 YYYY-MM-DD）+ 类型 + 分类
        （含子分类）+ 账户（含转账双侧）+ 标签 + 关键词（匹配备注与分类名）+ 金额区间。

        返回 {items: [...], total: N}；默认不含作废流水（include_voided=true 时含）。
        地点过滤（loc_city/loc_type/loc_biz/loc_poi_id 精确匹配 + loc_keyword
        对名称/地址模糊匹配）：对 loc 列 JSON 串提取匹配，未标注地点不命中；
        任一地点过滤参数生效时强制排除作废流水（软删除不可见于业务过滤），
        include_voided 仅在未使用地点过滤时生效。
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
            loc_city=loc_city, loc_type=loc_type, loc_biz=loc_biz,
            loc_poi_id=loc_poi_id, loc_keyword=loc_keyword,
            include_voided=include_voided, limit=limit, offset=offset,
        ).model_dump(mode="json")

    # ---------- M5 收支统计 ----------
    @mcp.tool()
    def summarize(
        start: str,
        end: str,
        group_by: Literal["category", "account", "tag", "day", "month",
                         "loc_city", "loc_adcode", "loc_type", "loc_biz"] = "category",
        direction: Optional[Literal["income", "expense"]] = None,
    ) -> dict:
        """区间收支统计（排除转账与不计收支项）。group_by 决定分组维度，
        返回各组 {key, name, income, expense, net}；direction 可只看收入或支出。
        group_by 另支持地点维度 loc_city/loc_adcode/loc_type/loc_biz
        （按 loc 列 JSON 串提取；NULL 统一归「未标注地点」组）。"""
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

    # ---------- 分类查询（先查后选，任务 01a14fbd） ----------
    @mcp.tool()
    def list_categories(
        direction: Optional[Literal["expense", "income"]] = None,
        include_disabled: bool = False,
    ) -> dict:
        """查在档分类列表（写入前先查后选：无合适分类再向 add/update 传新名称，
        写入时将自动创建一级分类）。direction 可只看支出（expense）或收入
        （income）分类；include_disabled=true 时含已停用分类（默认仅启用）。
        返回 {"categories": [...]}，每项含 id/name/parent_id/direction/
        enabled/is_preset/sort。"""
        cats = categories_svc.list_categories()
        if direction is not None:
            cats = [c for c in cats if c.direction == direction]
        if not include_disabled:
            cats = [c for c in cats if c.enabled]
        return {"categories": [c.model_dump(mode="json") for c in cats]}

    return mcp
