"""REST 薄层（FastAPI）：业务路由 + 静态托管 React 构建产物。

口径来源：架构设计方案 v1.0 §2.1/§2.3/§4 + UI 规范 §1（API 字段口径）
+ 总管只读模式评估报告 01a14009（评估面 a/c/d/g，落码单 01a14409，AMan 拍板①~⑤按推荐）：
- 仅绑定 127.0.0.1（默认端口 8723，env LEDGER_PORT 可配），独立本地 Web 应用。
- 契约 = FastAPI 自动产出的 OpenAPI schema（/openapi.json），前端 TS 类型经
  openapi-typescript 生成，不手编（TL common 包评估收口口径）。
- 业务逻辑一律下沉 services/；本文件只做路由、错误翻译与总管模式读编排/写凭证检查。
- 路由 sync def（duckdb 同步驱动，FastAPI 自动跑 threadpool）。

总管只读模式（LEDGER_CREDENTIAL 未设，serve 入口分流放行）：
- 读编排 _read_targets：单用户模式恒单目标 [None]（现行为逐字节不变）；
  总管模式 uid=None → 聚合全部已注册用户（per-user 异常 stderr 留痕跳过，
  损坏库不放大为全局 5xx；评估面 b 容错口径）；
  总管模式 uid=显式 → 锁定单库（前端按用户切换器）。
- 写检查 _write_credential（Depends 单点，恰 9 写端点全挂）：单用户模式放行
  （零行为变化）；总管模式要求请求级凭证 X-Ledger-Credential，复用 parse_credential
  六步校验链（不做双嗅探，拍板①）；缺失/无效=HTTP 401（拍板④）；
  每请求独立解析，零跨请求缓存。
- 读聚合走 read_only_scope()：read_only 连接不 mkdir 不建库，零副作用。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional, TypeVar

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import (
    USER_ID_PATTERN,
    CredentialError,
    db_path,
    parse_credential,
    read_only_scope,
    single_user_mode,
)
from .errors import LedgerError, NotFoundError
from .models import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BudgetSetRequest,
    BudgetStatusOut,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    OverviewOut,
    StatsOut,
    TagOut,
    TagRenameRequest,
    TransactionCreate,
    TransactionListOut,
    TransactionOut,
    TransactionUpdate,
    UserScoped,
)
from .services import accounts as accounts_svc
from .services import budget as budget_svc
from .services import categories as categories_svc
from .services import stats as stats_svc
from .services import tags as tags_svc
from .services import transactions as txns_svc
from .services.discovery import discover_user_ids

T = TypeVar("T")

_CREDENTIAL_HEADER = "X-Ledger-Credential"


def _error_payload(e: LedgerError) -> dict:
    return {"error": {"code": e.code, "message": e.message}}


def create_app() -> FastAPI:
    app = FastAPI(
        title="ledger",
        version="0.1.0",
        description="DuckDB MCP 个人流水账服务（REST 面；业务规则 R1-R7 唯一口径见 services/）",
    )

    # ---- 错误契约统一：LedgerError → HTTP 状态码 + {error:{code,message}} ----
    @app.exception_handler(LedgerError)
    async def _ledger_error_handler(_req, exc: LedgerError):
        return JSONResponse(status_code=exc.http_status, content=_error_payload(exc))

    # ---- 请求级凭证失败 → HTTP 401（评估面 c，AMan 拍板④按推荐）----
    # env_missing→credential_missing 仅请求语境文案翻译（「环境变量未设置」在
    # 请求语境语义不达）；MCP 启动语境 env_missing 原样保留。
    @app.exception_handler(CredentialError)
    async def _credential_error_handler(_req, exc: CredentialError):
        code = "credential_missing" if exc.code == "env_missing" else exc.code
        return JSONResponse(
            status_code=401,
            content={"error": {"code": code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_req, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": str(exc.errors()[:3])}},
        )

    # ---- 总管模式：写端点请求级凭证检查（Depends 单点，恰 9 写端点全挂）----
    def _write_credential(
        x_ledger_credential: Optional[str] = Header(default=None, alias=_CREDENTIAL_HEADER),
    ) -> Optional[str]:
        """返回显式 uid（总管模式，经六步校验链解析）；单用户模式返回 None 放行。

        缺头=credential_missing（请求语境文案，评估面 a 口径：env 语境语义不达）；
        值非法走 parse_credential 原错误码族（不做双嗅探，拍板①）。
        """
        if single_user_mode():
            return None
        if not x_ledger_credential:
            raise CredentialError(
                "credential_missing",
                "缺少写凭证：总管模式写操作需经 X-Ledger-Credential 请求头提供结构化 base64 凭证串",
            )
        return parse_credential(x_ledger_credential)

    def _read_targets(params: UserScoped) -> list[Optional[str]]:
        """读编排目标序列（评估面 d/g）：单用户模式=恒 [None]（uid 查询参数忽略=
        向后兼容零破坏）；总管模式 uid=显式 → 锁定单库（未注册/非法 uid=404/400）；
        总管模式 uid 未传=聚合全部已注册用户（空集=空聚合非报错）。"""
        if single_user_mode():
            return [None]
        if params.uid is not None:
            if not USER_ID_PATTERN.fullmatch(params.uid):
                raise NotFoundError(
                    "invalid_uid", f"uid 含非法字符（白名单 ^[A-Za-z0-9_-]+$）：{params.uid!r}")
            if not db_path(params.uid).exists():
                raise NotFoundError("user_not_found", f"用户库不存在：{params.uid}")
            return [params.uid]
        return discover_user_ids()

    def _read_scoped(targets: Iterable[Optional[str]],
                     call: Callable[..., T], *args, **kwargs) -> list[T]:
        """逐目标执行读调用：总管多目标进 read_only_scope（read_only 连接零副作用），
        per-user LedgerError stderr 留痕跳过（损坏库/被锁不放大为全局 5xx，评估面 b）。"""
        targets = list(targets)
        if targets == [None]:
            return [call(*args, **kwargs)]
        with read_only_scope():
            results: list[T] = []
            for uid in targets:
                try:
                    results.append(call(*args, uid=uid, **kwargs))
                except LedgerError as exc:
                    # per-user 容错（评估面 b）：损坏库/意外撞锁 stderr 留痕跳过，
                    # 不放大为全局 5xx；空集/全跳过=空聚合非报错。
                    print(f"ledger 总管读聚合：跳过不可读库 uid={uid} code={exc.code}",
                          file=sys.stderr)
            return results

    # ---- 健康 ----
    @app.get("/api/health")
    def health() -> dict:
        from .db import current_user_id
        return {
            "status": "ok",
            "service": "ledger",
            "mode": "single" if single_user_mode() else "manager",
            "user": current_user_id(),
        }

    # ---- 已注册用户清单（总管模式切换器数据源；单用户模式恒空=切换器不渲染）----
    @app.get("/api/v1/users")
    def list_users() -> dict:
        if single_user_mode():
            return {"items": []}
        return {"items": discover_user_ids()}

    # ---- 交易（M1-M4 对应的 UI 写读面）----
    @app.post("/api/v1/transactions", status_code=status.HTTP_201_CREATED,
              response_model=TransactionOut)
    def create_transaction(params: TransactionCreate,
                           uid: Optional[str] = Depends(_write_credential)) -> TransactionOut:
        return txns_svc.create(params, uid=uid)

    @app.get("/api/v1/transactions", response_model=TransactionListOut)
    def list_transactions_endpoint(
        start: Optional[str] = None,
        end: Optional[str] = None,
        type: Optional[str] = Query(default=None, pattern="^(expense|income|transfer)$"),
        category_id: Optional[str] = None,
        account_id: Optional[str] = None,
        tag: Optional[list[str]] = Query(default=None),
        keyword: Optional[str] = None,
        min_amount: Optional[str] = None,
        max_amount: Optional[str] = None,
        include_voided: bool = False,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        scope: UserScoped = Depends(),
    ) -> TransactionListOut:
        targets = _read_targets(scope)
        totals = 0
        items: list[TransactionOut] = []
        for uid in targets:
            out = txns_svc.list_transactions(
                start=start, end=end, txn_type=type, category_id=category_id,
                account_id=account_id, tag=tag, keyword=keyword,
                min_amount=min_amount, max_amount=max_amount,
                include_voided=include_voided, limit=limit, offset=offset,
                uid=uid,
            )
            items.extend(out.items)
            totals += out.total
        items.sort(key=lambda t: t.occurred_at, reverse=True)
        return TransactionListOut(items=items, total=totals)

    @app.get("/api/v1/transactions/{txn_id}", response_model=TransactionOut)
    def get_transaction_endpoint(txn_id: str, scope: UserScoped = Depends()) -> TransactionOut:
        targets = _read_targets(scope)
        if len(targets) == 1:
            return txns_svc.get(txn_id, uid=targets[0])
        # 聚合形态：逐库查（命中即返，防跨库同 id 语义失真——id 系 uuid4 全局唯一，
        # 实务上首库即命中；保持线性扫描语义清晰）
        from .db import read_only_scope
        with read_only_scope():
            for uid in targets:
                try:
                    return txns_svc.get(txn_id, uid=uid)
                except LedgerError as exc:
                    print(f"ledger 总管读聚合：跳过不可读库 uid={uid} code={exc.code}",
                          file=sys.stderr)
        raise LedgerError("transaction_not_found", f"流水不存在：{txn_id}", http_status=404)

    @app.patch("/api/v1/transactions/{txn_id}", response_model=TransactionOut)
    def update_transaction_endpoint(txn_id: str, params: TransactionUpdate,
                                    uid: Optional[str] = Depends(_write_credential)) -> TransactionOut:
        return txns_svc.update(txn_id, params, uid=uid)

    @app.post("/api/v1/transactions/{txn_id}/void", response_model=TransactionOut)
    def void_transaction_endpoint(txn_id: str,
                                  uid: Optional[str] = Depends(_write_credential)) -> TransactionOut:
        return txns_svc.void(txn_id, uid=uid)

    # ---- 概览（U2）----
    @app.get("/api/v1/overview", response_model=OverviewOut)
    def overview_endpoint(month: Optional[str] = None,
                          scope: UserScoped = Depends()) -> OverviewOut:
        targets = _read_targets(scope)
        outs = _read_scoped(targets, stats_svc.overview, month)
        if len(outs) == 1:
            return outs[0]
        income = sum((o.income for o in outs), start=0) if outs else 0
        expense = sum((o.expense for o in outs), start=0) if outs else 0
        month_label = month or (outs[0].month if outs else "")
        return OverviewOut(month=month_label, income=income, expense=expense,
                           balance=income - expense)

    # ---- 账户（U5）----
    @app.get("/api/v1/accounts", response_model=list[AccountOut])
    def list_accounts_endpoint(scope: UserScoped = Depends()) -> list[AccountOut]:
        targets = _read_targets(scope)
        outs = _read_scoped(targets, accounts_svc.list_accounts)
        return [a for out in outs for a in out]

    @app.post("/api/v1/accounts", status_code=status.HTTP_201_CREATED,
              response_model=AccountOut)
    def create_account_endpoint(params: AccountCreate,
                                uid: Optional[str] = Depends(_write_credential)) -> AccountOut:
        return accounts_svc.create_account(params, uid=uid)

    @app.patch("/api/v1/accounts/{account_id}", response_model=AccountOut)
    def update_account_endpoint(account_id: str, params: AccountUpdate,
                                uid: Optional[str] = Depends(_write_credential)) -> AccountOut:
        return accounts_svc.update_account(account_id, params, uid=uid)

    # ---- 分类（U5 次级 Tab：两级树 + 预置集保护）----
    @app.get("/api/v1/categories", response_model=list[CategoryOut])
    def list_categories_endpoint(scope: UserScoped = Depends()) -> list[CategoryOut]:
        targets = _read_targets(scope)
        outs = _read_scoped(targets, categories_svc.list_categories)
        return [c for out in outs for c in out]

    @app.post("/api/v1/categories", status_code=status.HTTP_201_CREATED,
              response_model=CategoryOut)
    def create_category_endpoint(params: CategoryCreate,
                                 uid: Optional[str] = Depends(_write_credential)) -> CategoryOut:
        return categories_svc.create_category(params, uid=uid)

    @app.patch("/api/v1/categories/{category_id}", response_model=CategoryOut)
    def update_category_endpoint(category_id: str, params: CategoryUpdate,
                                 uid: Optional[str] = Depends(_write_credential)) -> CategoryOut:
        return categories_svc.update_category(category_id, params, uid=uid)

    # ---- 标签 ----
    @app.get("/api/v1/tags", response_model=list[TagOut])
    def list_tags_endpoint(scope: UserScoped = Depends()) -> list[TagOut]:
        targets = _read_targets(scope)
        outs = _read_scoped(targets, tags_svc.list_tags)
        return [t for out in outs for t in out]

    @app.patch("/api/v1/tags/{tag_id}", response_model=TagOut)
    def rename_tag_endpoint(tag_id: str, params: TagRenameRequest,
                            uid: Optional[str] = Depends(_write_credential)) -> TagOut:
        return tags_svc.rename_tag(tag_id, params.name, uid=uid)

    # ---- 预算（U4）----
    @app.get("/api/v1/budgets", response_model=BudgetStatusOut)
    def budget_status_endpoint(period: Optional[str] = None,
                               scope: UserScoped = Depends()) -> BudgetStatusOut:
        targets = _read_targets(scope)
        outs = _read_scoped(targets, budget_svc.budget_status, period)
        if len(outs) == 1:
            return outs[0]
        period_label = period or (outs[0].period if outs else "")
        return BudgetStatusOut(period=period_label,
                               lines=[l for out in outs for l in out.lines])

    @app.put("/api/v1/budgets", response_model=BudgetStatusOut)
    def set_budgets_endpoint(params: BudgetSetRequest,
                             uid: Optional[str] = Depends(_write_credential)) -> BudgetStatusOut:
        return budget_svc.set_budgets(params, uid=uid)

    # ---- 统计（U3 数据源）----
    @app.get("/api/v1/stats/summary", response_model=StatsOut)
    def stats_summary_endpoint(
        start: str,
        end: str,
        group_by: str = Query(default="category",
                              pattern="^(category|account|tag|day|month)$"),
        direction: Optional[str] = Query(default=None, pattern="^(income|expense)$"),
        category_id: Optional[str] = None,
        account_id: Optional[str] = None,
        tag: Optional[list[str]] = Query(default=None),
        scope: UserScoped = Depends(),
    ) -> StatsOut:
        targets = _read_targets(scope)
        outs = _read_scoped(targets, stats_svc.summary, start=start, end=end,
                            group_by=group_by, direction=direction,
                            category_id=category_id, account_id=account_id, tag=tag)
        if len(outs) == 1:
            return outs[0]
        items = [g for out in outs for g in out.items]
        items.sort(key=lambda g: g.expense + g.income, reverse=True)
        first = outs[0] if outs else None
        return StatsOut(start=first.start if first else start,
                        end=first.end if first else end,
                        group_by=group_by, items=items)

    # ---- 静态托管 React 构建产物（构建生成不入手编；存在才挂载）----
    ui_dir = Path(__file__).resolve().parent / "ui"
    if (ui_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    else:
        @app.get("/")
        def index() -> dict:
            return {"service": "ledger", "status": "ui_not_built",
                    "hint": "前端构建产物 ledger/ui/ 不存在（执行单②产出后自动托管）"}

    return app


app = create_app()
