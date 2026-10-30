"""services.categories — 两级分类树约束 + 预置集保护（R6）。

口径来源：产品调研报告 R6 + 架构设计方案 §3：
- 两级封顶（一级→二级，二级不可再有子级）；子分类方向继承父级。
- 预置集（is_preset，§12.6 拍板 14 类）：禁删只可停用——REST 管理面另设
  「受保护归档」端点（archive_category），双闸门（非预置 + 零引用）后方可物理
  移除；MCP 工具面不提供任何删除动作。
- 同级重名冲突兜底（DuckDB 对含 NULL 的 UNIQUE 不去重，服务层强制）。

引用判别顺序（缺陷修复批次 01a18c71）：所有「引用 → ID」解析一律**先按主键
精确查、未命中再按名称查**。原实现只按名称查，把 seed 命名空间的主键字面量
（cat_exp_01 / cat_inc_01 等，见 schema.sql seed）当成「分类名称」，写路径随即
自动建档出一条同名垃圾分类（实锚：污染分类 6bdc4552-145a-4862-9c2d-8cdde3884c62
/ name='cat_inc_01' / 零引用）。id 是主键、名称不是——形态语法（是否 UUID）
不构成判别依据。
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..db import connect
from ..errors import ConflictError, LedgerError, NotFoundError
from ..models import CategoryCreate, CategoryOut, CategoryUpdate

_COLS = "id, name, parent_id, direction, enabled, is_preset, sort"


def _out(row) -> CategoryOut:
    return CategoryOut(id=row[0], name=row[1], parent_id=row[2], direction=row[3],
                       enabled=row[4], is_preset=row[5], sort=row[6])


def list_categories(uid: str | None = None) -> list[CategoryOut]:
    with connect(uid) as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM categories ORDER BY direction, sort, name"
        ).fetchall()
    return [_out(r) for r in rows]


def create_category(params: CategoryCreate, uid: str | None = None) -> CategoryOut:
    with connect(uid) as conn:
        direction = params.direction
        if params.parent_id:
            prow = conn.execute(
                "SELECT parent_id, direction FROM categories WHERE id = ?",
                [params.parent_id]).fetchone()
            if prow is None:
                raise NotFoundError("category_not_found", f"父分类不存在：{params.parent_id}")
            if prow[0] is not None:
                raise LedgerError("category_depth_exceeded", "分类最多两级（R6）")
            direction = prow[1]  # 子分类方向继承父级
            if params.direction != direction:
                raise LedgerError("category_direction_mismatch", "子分类方向须与父分类一致")
        if params.parent_id is None:
            # 根级按「同名+同方向」判重（direction 是分类主分区：支出/收入可同名，seed 的「其他」即此）
            dup = conn.execute(
                "SELECT 1 FROM categories WHERE name = ? AND parent_id IS NULL"
                " AND direction = ?",
                [params.name, direction]).fetchone()
        else:
            dup = conn.execute(
                "SELECT 1 FROM categories WHERE name = ? AND parent_id = ?",
                [params.name, params.parent_id]).fetchone()
        if dup:
            raise ConflictError("category_name_taken", f"同级分类名已存在：{params.name}")
        cid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO categories (id, name, parent_id, direction, enabled, is_preset, sort)"
            " VALUES (?, ?, ?, ?, TRUE, FALSE, 0)",
            [cid, params.name, params.parent_id, direction],
        )
        row = conn.execute(f"SELECT {_COLS} FROM categories WHERE id = ?", [cid]).fetchone()
        return _out(row)


def update_category(cid: str, params: CategoryUpdate, uid: str | None = None) -> CategoryOut:
    """可改项：名称/启停/排序；direction 创建后不可改（存量流水口径一致性）。
    预置集允许停用（R6：停用≠删除），本函数不提供删除。"""
    with connect(uid) as conn:
        row = conn.execute(
            "SELECT parent_id FROM categories WHERE id = ?", [cid]).fetchone()
        if row is None:
            raise NotFoundError("category_not_found", f"分类不存在：{cid}")
        if params.name is not None:
            parent = row[0]
            if parent is None:
                dup = conn.execute(
                    "SELECT 1 FROM categories WHERE name = ? AND parent_id IS NULL"
                    " AND direction = (SELECT direction FROM categories WHERE id = ?)"
                    " AND id <> ?",
                    [params.name, cid, cid]).fetchone()
            else:
                dup = conn.execute(
                    "SELECT 1 FROM categories WHERE name = ? AND parent_id = ? AND id <> ?",
                    [params.name, parent, cid]).fetchone()
            if dup:
                raise ConflictError("category_name_taken", f"同级分类名已存在：{params.name}")
        sets, args = [], []
        if params.name is not None:
            sets.append("name = ?")
            args.append(params.name)
        if params.enabled is not None:
            sets.append("enabled = ?")
            args.append(params.enabled)
        if params.sort is not None:
            sets.append("sort = ?")
            args.append(params.sort)
        if sets:
            args.append(cid)
            conn.execute(f"UPDATE categories SET {', '.join(sets)} WHERE id = ?", args)
        return _out(conn.execute(
            f"SELECT {_COLS} FROM categories WHERE id = ?", [cid]).fetchone())


def _is_uuid_form(ref: str) -> bool:
    """ID 形态判别（**只用于「未命中时是否允许自动建档」的决策**）。

    不作为「ID vs 名称」的分流判据（缺陷修复批次 01a18c71）：seed 命名空间的
    主键字面量（cat_exp_01 / cat_inc_01 / acc_default_cash 等）不是 UUID 形态，
    却是完全合法的主键——用语法形态判分流＝把合法主键当名称。
    """
    try:
        uuid.UUID(str(ref))
        return True
    except ValueError:
        return False


def resolve_category_id(ref: str, direction: Optional[str] = None) -> str:
    """分类引用解析：**先按主键精确查，未命中再按名称查**（同向多命中报歧义）。

    判别顺序（缺陷修复批次 01a18c71）：主键命中优先且不再进入名称歧义判定——
    id 是唯一标识，name 不是（支出/收入可同名，如 seed 的「其他」）。

    direction 传入时仅在按名称解析的同方向内查询（支出/收入分类可同名）。
    """
    with connect() as conn:
        row = conn.execute("SELECT id FROM categories WHERE id = ?", [ref]).fetchone()
        if row is not None:
            return row[0]
        q = "SELECT id FROM categories WHERE name = ?"
        args: list = [ref]
        if direction is not None:
            q += " AND direction = ?"
            args.append(direction)
        rows = conn.execute(q + " ORDER BY sort, name", args).fetchall()
    if not rows:
        raise NotFoundError("category_not_found", f"分类不存在：{ref}")
    if len(rows) > 1:
        raise LedgerError(
            "ambiguous_category_name",
            f"分类名「{ref}」命中多条，请改用分类 ID（可先经 REST /categories 查询）",
        )
    return rows[0][0]


def resolve_or_create_category_id(ref: str, txn_type: Optional[str]) -> str:
    """MCP 写入面 get-or-create（任务 01a14fbd·拍板①②③④，严格对齐账户先例
    resolve_or_create_account_id 形态）。

    **判别顺序（缺陷修复批次 01a18c71·doc/代码漂移一并修正）**：本函数文档原
    声称「先走既有解析（ID 直查/名称按向查）」，但代码只有名称分支——现由
    resolve_category_id 真正实现「ID 精确查优先、未命中再按名称」，本函数因此
    获得「id 与 name 双未命中」这一准确语义，再按下面四条决定后续：

    - 双未命中 + 方向不可判定（txn_type=None 局部更新/查询面）+ ID 形态 →
      原样透传（对齐既有「ID 直传不校验存在性」语义：查询面落空集、收款方
      与转账由写路径 transactions._validate 自行判定，零行为变更）；
    - 双未命中 + 方向不可判定 + 名称形态 → 只解析不建档，抛 category_not_found
      （update 不传 txn_type 改既有分类名等既有契约零回归）；
    - 双未命中 + 方向可判定（expense/income）+ ID 形态 → 抛 category_not_found
      （拍板④：ID 直传仍严格不建档）；
    - 双未命中 + 方向可判定 + 名称形态 → create_category 自动建档：name=ref /
      parent_id=None（一律一级挂载，拍板③）/ direction 继承 txn_type（拍板③）/
      enabled=True / is_preset=False / sort=0 / id=uuid4（cat_exp_N 系预置
      seed 专用前缀禁用，拍板④）。

    命中即返回——含停用分类（不复活不新建，停用写入由写路径既有校验报
    category_disabled）。并发建档撞名兜底沿账户先例「撞名即幂等」语义：重查
    返回既有。REST 管理面不经过本函数（显式管理动作维持强校验）。
    """
    ref = str(ref)
    is_uuid = _is_uuid_form(ref)
    creatable = txn_type in ("expense", "income")
    try:
        return resolve_category_id(ref, direction=txn_type if creatable else None)
    except NotFoundError:
        if is_uuid and not creatable:
            return ref  # 方向不可判定 + ID 形态：原样透传（见 docstring 第 1 条）
        if is_uuid or not creatable:
            raise  # UUID 直传严格不建档；方向不可判定只解析不建档
        try:
            created = create_category(CategoryCreate(
                name=ref, parent_id=None, direction=txn_type))
        except ConflictError:
            # 并发建档撞名兜底（账户先例幂等语义）：categories 无 UNIQUE 约束
            # （schema 注释明示同父重名由服务层兜底），撞名载体为
            # create_category 同向 dup 检查 → ConflictError，重查返回既有。
            return resolve_category_id(ref, direction=txn_type)
        return created.id


def archive_category(cid: str, uid: str | None = None) -> CategoryOut:
    """受保护归档（缺陷修复批次 01a18c71·C 案）：预置集禁删 + 零引用双闸门。

    R6「预置集禁删只可停用」不变：is_preset=TRUE 一律拒（category_preset_protected），
    存量 14 类预置分类永不因此端点消失。

    「零引用」按**三条独立引用面**逐一判定（任一非零即拒）：
    1) 子分类 categories.parent_id —— 否则删父留孤儿子分类、两级树断裂；
    2) 流水 transactions.category_id —— 含作废笔（voided 仍是有效外键引用，
       不筛 status）；否则 DELETE 直接撞外键；
    3) 预算 budgets.category_id —— 同表外键，常被遗漏的第三条引用面。

    通过后**物理移除**并返回移除前快照（id/name/parent_id/direction/enabled/
    is_preset/sort 全量）：快照即人工恢复依据。本端点仅对 REST 管理面开放，
    MCP 工具面不提供（配置类增删改不进 MCP，防工具面膨胀——调研口径）。
    """
    with connect(uid) as conn:
        row = conn.execute(f"SELECT {_COLS} FROM categories WHERE id = ?", [cid]).fetchone()
        if row is None:
            raise NotFoundError("category_not_found", f"分类不存在：{cid}")
        snap = _out(row)
        if snap.is_preset:
            raise ConflictError(
                "category_preset_protected",
                f"预置分类禁删只可停用（R6）：{snap.name}")
        children = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE parent_id = ?", [cid]).fetchone()[0]
        if children:
            raise ConflictError(
                "category_has_children",
                f"分类下仍有 {children} 个子分类，须先归档子分类：{snap.name}")
        txns = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE category_id = ?", [cid]).fetchone()[0]
        if txns:
            raise ConflictError(
                "category_in_use",
                f"分类仍被 {txns} 笔流水引用（含作废笔），不可归档：{snap.name}")
        budgets = conn.execute(
            "SELECT COUNT(*) FROM budgets WHERE category_id = ?", [cid]).fetchone()[0]
        if budgets:
            raise ConflictError(
                "category_in_use",
                f"分类仍被 {budgets} 条预算引用，不可归档：{snap.name}")
        conn.execute("DELETE FROM categories WHERE id = ?", [cid])
        return snap
