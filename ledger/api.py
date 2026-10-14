"""REST 薄层（FastAPI）：业务路由 + 静态托管 React 构建产物。

口径来源：架构设计方案 v1.0 §2.1/§2.3/§4 + UI 规范 §1（API 字段口径）：
- 仅绑定 127.0.0.1（默认端口 8723，env LEDGER_PORT 可配），独立本地 Web 应用。
- 契约 = FastAPI 自动产出的 OpenAPI schema（/openapi.json），前端 TS 类型经
  openapi-typescript 生成，不手编（TL common 包评估收口口径）。
- 业务逻辑一律下沉 services/；本文件只做路由与错误翻译（LedgerError→HTTP 状态码）。
- 路由 sync def（duckdb 同步驱动，FastAPI 自动跑 threadpool）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .errors import LedgerError
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
)
from .services import accounts as accounts_svc
from .services import budget as budget_svc
from .services import categories as categories_svc
from .services import stats as stats_svc
from .services import tags as tags_svc
from .services import transactions as txns_svc


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

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_req, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": str(exc.errors()[:3])}},
        )

    # ---- 健康 ----
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "service": "ledger"}

    # ---- 交易（M1-M4 对应的 UI 写读面）----
    @app.post("/api/v1/transactions", status_code=status.HTTP_201_CREATED,
              response_model=TransactionOut)
    def create_transaction(params: TransactionCreate) -> TransactionOut:
        return txns_svc.create(params)

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
    ) -> TransactionListOut:
        return txns_svc.list_transactions(
            start=start, end=end, txn_type=type, category_id=category_id,
            account_id=account_id, tag=tag, keyword=keyword,
            min_amount=min_amount, max_amount=max_amount,
            include_voided=include_voided, limit=limit, offset=offset,
        )

    @app.get("/api/v1/transactions/{txn_id}", response_model=TransactionOut)
    def get_transaction_endpoint(txn_id: str) -> TransactionOut:
        return txns_svc.get(txn_id)

    @app.patch("/api/v1/transactions/{txn_id}", response_model=TransactionOut)
    def update_transaction_endpoint(txn_id: str, params: TransactionUpdate) -> TransactionOut:
        return txns_svc.update(txn_id, params)

    @app.post("/api/v1/transactions/{txn_id}/void", response_model=TransactionOut)
    def void_transaction_endpoint(txn_id: str) -> TransactionOut:
        return txns_svc.void(txn_id)

    # ---- 概览（U2）----
    @app.get("/api/v1/overview", response_model=OverviewOut)
    def overview_endpoint(month: Optional[str] = None) -> OverviewOut:
        return stats_svc.overview(month)

    # ---- 账户（U5）----
    @app.get("/api/v1/accounts", response_model=list[AccountOut])
    def list_accounts_endpoint() -> list[AccountOut]:
        return accounts_svc.list_accounts()

    @app.post("/api/v1/accounts", status_code=status.HTTP_201_CREATED,
              response_model=AccountOut)
    def create_account_endpoint(params: AccountCreate) -> AccountOut:
        return accounts_svc.create_account(params)

    @app.patch("/api/v1/accounts/{account_id}", response_model=AccountOut)
    def update_account_endpoint(account_id: str, params: AccountUpdate) -> AccountOut:
        return accounts_svc.update_account(account_id, params)

    # ---- 分类（U5 次级 Tab：两级树 + 预置集保护）----
    @app.get("/api/v1/categories", response_model=list[CategoryOut])
    def list_categories_endpoint() -> list[CategoryOut]:
        return categories_svc.list_categories()

    @app.post("/api/v1/categories", status_code=status.HTTP_201_CREATED,
              response_model=CategoryOut)
    def create_category_endpoint(params: CategoryCreate) -> CategoryOut:
        return categories_svc.create_category(params)

    @app.patch("/api/v1/categories/{category_id}", response_model=CategoryOut)
    def update_category_endpoint(category_id: str, params: CategoryUpdate) -> CategoryOut:
        return categories_svc.update_category(category_id, params)

    # ---- 标签 ----
    @app.get("/api/v1/tags", response_model=list[TagOut])
    def list_tags_endpoint() -> list[TagOut]:
        return tags_svc.list_tags()

    @app.patch("/api/v1/tags/{tag_id}", response_model=TagOut)
    def rename_tag_endpoint(tag_id: str, params: TagRenameRequest) -> TagOut:
        return tags_svc.rename_tag(tag_id, params.name)

    # ---- 预算（U4）----
    @app.get("/api/v1/budgets", response_model=BudgetStatusOut)
    def budget_status_endpoint(period: Optional[str] = None) -> BudgetStatusOut:
        return budget_svc.budget_status(period)

    @app.put("/api/v1/budgets", response_model=BudgetStatusOut)
    def set_budgets_endpoint(params: BudgetSetRequest) -> BudgetStatusOut:
        return budget_svc.set_budgets(params)

    # ---- 统计（U3 数据源）----
    @app.get("/api/v1/stats/summary", response_model=StatsOut)
    def stats_summary_endpoint(
        start: str,
        end: str,
        group_by: str = Query(default="category",
                              pattern="^(category|account|tag|day|month)$"),
        direction: Optional[str] = Query(default=None, pattern="^(income|expense)$"),
    ) -> StatsOut:
        return stats_svc.summary(start=start, end=end, group_by=group_by,
                                 direction=direction)

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
