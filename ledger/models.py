"""pydantic 模型单一事实源：MCP 工具参数 / REST 请求体 / 响应契约共用。

口径来源：架构设计方案 v1.0 §4 + TL common 包评估收口（双入口契约同源：
FastAPI 自动产出的 OpenAPI schema 即契约，前端 TS 类型经 openapi-typescript 生成，
不手编；MCP 工具面按薄层平铺参数构造同一模型，杜绝口径漂移）。

金额口径（全模块统一）：Decimal(precision 18, scale 2, >0, R1)；JSON 序列化为
字符串两位小数（float 会引入二进制误差，记账系统禁用 float 承载金额）。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

TxnType = Literal["expense", "income", "transfer"]
AccountType = Literal["cash", "debit", "credit", "virtual"]
Direction = Literal["expense", "income"]
BudgetStatus = Literal["normal", "near_limit", "over"]

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


class UserScoped(BaseModel):
    """读端点可选用户切换（总管模式评估面 d，AMan 拍板②按推荐）。

    uid=None：单用户模式=本库（现行为）；总管模式=聚合全部已注册用户。
    uid=显式：总管模式锁定单库；单用户模式忽略（向后兼容零破坏）。
    写端点不加 uid（写目标一律由请求级凭证解析，防贴他人凭证错写）。
    """

    uid: Optional[str] = None


class LocationInfo(BaseModel):
    """地点信息结构体（高德地理语义，任务 01a14d06·AMan 拍板②修正）。

    9 键无前缀单一事实源：对外（MCP 工具参数 / REST 请求响应）=内部（服务层）=
    存储（loc 列 JSON 串）同一套结构体协议。全键 Optional，三级空值语义：
    整体 None=无地点 / 部分填充（如仅 raw）/ 完整 9 键。
    point 为 "经度,纬度" 串（GCJ-02 原值直存直出零变换，正则防格式漂移与注入）。
    """

    name: Optional[str] = Field(default=None, max_length=128)
    city: Optional[str] = Field(default=None, max_length=64)
    point: Optional[str] = Field(
        default=None, pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")
    poi_id: Optional[str] = Field(default=None, max_length=32)
    adcode: Optional[str] = Field(default=None, max_length=16)
    type: Optional[str] = Field(default=None, max_length=64)
    biz: Optional[str] = Field(default=None, max_length=64)
    addr: Optional[str] = Field(default=None, max_length=256)
    raw: Optional[str] = Field(default=None, max_length=256)


class TransactionCreate(BaseModel):
    """记一笔（M1）：R1 必填约束在服务层按类型细化（转账无分类、需对方账户）。"""

    type: TxnType
    amount: Decimal = Field(..., gt=Decimal("0"), max_digits=18, decimal_places=2)
    category_id: Optional[str] = None
    account_id: str
    counter_account_id: Optional[str] = None
    occurred_at: Optional[datetime] = None  # R1：缺省=当下（服务层填充）
    note: Optional[str] = None
    in_sum: bool = True  # R4：资金腾挪类置 False
    tags: list[str] = Field(default_factory=list)
    location: Optional[LocationInfo] = None  # 高德地理语义（拍板②修正：结构体统一协议）


class TransactionUpdate(BaseModel):
    """修改（M2）：全可选，None=不变更；服务层合并终态后按 R1-R4 重校验。"""

    type: Optional[TxnType] = None
    amount: Optional[Decimal] = Field(default=None, gt=Decimal("0"), max_digits=18, decimal_places=2)
    category_id: Optional[str] = None
    account_id: Optional[str] = None
    counter_account_id: Optional[str] = None
    occurred_at: Optional[datetime] = None
    note: Optional[str] = None
    in_sum: Optional[bool] = None
    tags: Optional[list[str]] = None
    location: Optional[LocationInfo] = None  # None=不变更（与既有字段语义一致）


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class TagRenameRequest(BaseModel):
    """标签重命名（UI 配置管理）。"""

    name: str = Field(..., min_length=1, max_length=32)


class TransactionOut(BaseModel):
    """流水详情（M4 / 列表行）；余额、统计、UI 展示共用同一口径。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: TxnType
    amount: Decimal
    currency: str
    category_id: Optional[str] = None
    account_id: str
    counter_account_id: Optional[str] = None
    occurred_at: datetime
    note: Optional[str] = None
    in_sum: bool
    status: Literal["active", "voided"]
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut] = Field(default_factory=list)
    location: Optional[LocationInfo] = None  # loc 列回读（NULL=无地点）

    @field_serializer("amount")
    def _amount_str(self, v: Decimal) -> str:
        return f"{v:.2f}"


class TransactionListOut(BaseModel):
    """查流水（M3）：时间倒序 + 分页；total=当前筛选匹配总数。"""

    items: list[TransactionOut]
    total: int


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: AccountType
    currency: str = "CNY"  # R2：单一本位币起步
    initial_balance: Decimal = Field(default=Decimal("0"), max_digits=18, decimal_places=2)


class AccountUpdate(BaseModel):
    """账户可改项克制：名称与启停（销户语义复杂化，P0/P1 无删除动作，UI 规范 §5.3）。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    enabled: Optional[bool] = None


class AccountOut(BaseModel):
    """账户（余额=派生计算实时值，不落冗余列——防口径漂移关键设计）。"""

    id: str
    name: str
    type: AccountType
    currency: str
    initial_balance: Decimal
    enabled: bool
    balance: Decimal

    @field_serializer("initial_balance", "balance")
    def _money_str(self, v: Decimal) -> str:
        return f"{v:.2f}"


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    parent_id: Optional[str] = None  # NULL=一级；两级封顶由服务层强制（R6）
    direction: Direction


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=32)
    enabled: Optional[bool] = None
    sort: Optional[int] = None
    # direction 创建后不可改（存量流水口径一致性）；预置集禁删只可停（R6）


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    parent_id: Optional[str] = None
    direction: Direction
    enabled: bool
    is_preset: bool
    sort: int


class BudgetSetItem(BaseModel):
    """单条预算设定：category_id=None=总预算（E5：NULL 分类）。"""

    category_id: Optional[str] = None
    amount: Decimal = Field(..., gt=Decimal("0"), max_digits=18, decimal_places=2)


class BudgetSetRequest(BaseModel):
    """设预算（U4）：只设金额、固定按月；PUT 语义=整体替换该月预算集。"""

    period: str = Field(..., pattern=_PERIOD_RE.pattern)  # 'YYYY-MM'
    items: list[BudgetSetItem] = Field(..., min_length=1)


class BudgetLine(BaseModel):
    category_id: Optional[str] = None  # None=总预算行
    category_name: Optional[str] = None
    amount: Decimal
    used: Decimal
    remaining: Decimal
    status: BudgetStatus  # R7 三态：normal / near_limit(>=80%) / over(>100%)

    @field_serializer("amount", "used", "remaining")
    def _money_str(self, v: Decimal) -> str:
        return f"{v:.2f}"


class BudgetStatusOut(BaseModel):
    period: str
    lines: list[BudgetLine]


class OverviewOut(BaseModel):
    """本月收支概览（U2）：口径=排除转账（R3）与不计收支项（R4）；结余=收-支。"""

    month: str  # 'YYYY-MM'
    income: Decimal
    expense: Decimal
    balance: Decimal

    @field_serializer("income", "expense", "balance")
    def _money_str(self, v: Decimal) -> str:
        return f"{v:.2f}"


class GroupStat(BaseModel):
    """分组统计行（M5）：group_by 维度键 + 收支净额。"""

    key: Optional[str] = None  # 维度键（分类/账户 id、日期串）；tag 维度无实体 id 时=None
    name: Optional[str] = None
    income: Decimal
    expense: Decimal
    net: Decimal

    @field_serializer("income", "expense", "net")
    def _money_str(self, v: Decimal) -> str:
        return f"{v:.2f}"


class StatsOut(BaseModel):
    start: str
    end: str
    group_by: str
    items: list[GroupStat]
