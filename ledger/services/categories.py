"""services.categories — 两级分类树约束 + 预置集保护（R6）。

口径来源：产品调研报告 R6 + 架构设计方案 §3：
- 两级封顶（一级→二级，二级不可再有子级）；子分类方向继承父级。
- 预置集（is_preset，§12.6 拍板 14 类）：禁删只可停用——本服务不提供删除动作。
- 同级重名冲突兜底（DuckDB 对含 NULL 的 UNIQUE 不去重，服务层强制）。
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
    预置集允许停用（R6：停用≠删除），本服务不提供删除。"""
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


def resolve_category_id(name: str, direction: Optional[str] = None) -> str:
    """语义参数解析（MCP 面）：分类名→ID；同向多命中报歧义（提示改用 ID）。

    direction 传入时仅在同方向内解析（支出/收入分类可同名，如「其他」）。
    """
    with connect() as conn:
        q = "SELECT id FROM categories WHERE name = ?"
        args: list = [name]
        if direction is not None:
            q += " AND direction = ?"
            args.append(direction)
        rows = conn.execute(q + " ORDER BY sort, name", args).fetchall()
    if not rows:
        raise NotFoundError("category_not_found", f"分类不存在：{name}")
    if len(rows) > 1:
        raise LedgerError(
            "ambiguous_category_name",
            f"分类名「{name}」命中多条，请改用分类 ID（可先经 REST /categories 查询）",
        )
    return rows[0][0]


def resolve_or_create_category_id(ref: str, txn_type: Optional[str]) -> str:
    """MCP 写入面 get-or-create（任务 01a14fbd·拍板①②③④，严格对齐账户先例
    resolve_or_create_account_id 形态）：先走既有解析（ID 直查/名称按向查/
    歧义报错），命中即返回——含停用分类（不复活不新建，停用写入由写路径
    既有校验报 category_disabled）；未命中且 ref 为名称形态（非 UUID）且
    方向可判定（expense/income）→ create_category 自动建档：name=ref /
    parent_id=None（一律一级挂载，拍板③）/ direction 继承 txn_type（拍板③）/
    enabled=True / is_preset=False / sort=0 / id=uuid4（cat_exp_N 系预置
    seed 专用前缀禁用，拍板④）；未命中且 ref 为 UUID 形态→原样抛
    category_not_found（拍板④：ID 直传仍严格不建档）；方向不可判定
    （txn_type=None 防御口径 / transfer 不挂分类语义）→ 只解析不建档，
    未命中抛 category_not_found（update 不传 txn_type 改既有分类名等既有
    契约行为零回归）；并发建档撞名兜底沿账户先例「撞名即幂等」语义：
    重查返回既有。REST 管理面不经过本函数（显式管理动作维持强校验）。
    """
    is_uuid = False
    try:
        uuid.UUID(str(ref))
        is_uuid = True
    except ValueError:
        pass
    creatable = txn_type in ("expense", "income")
    try:
        return resolve_category_id(
            str(ref), direction=txn_type if creatable else None)
    except NotFoundError:
        if is_uuid or not creatable:
            raise  # UUID 直传严格不建档；方向不可判定只解析不建档
        try:
            created = create_category(CategoryCreate(
                name=str(ref), parent_id=None, direction=txn_type))
        except ConflictError:
            # 并发建档撞名兜底（账户先例幂等语义）：categories 无 UNIQUE 约束
            # （schema 注释明示同父重名由服务层兜底），撞名载体为
            # create_category 同向 dup 检查 → ConflictError，重查返回既有。
            return resolve_category_id(str(ref), direction=txn_type)
        return created.id
