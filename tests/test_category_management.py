"""分类管理能力测试（任务 01a14fbd·AMan 拍板七项全采+增补修复项）。

覆盖：MCP list_categories 三态（全量/direction/include_disabled）、写入面
get-or-create 动态建档（一级挂载/方向继承/预置标记/sort/闭环）、同向重名
约束不变、UUID 直传严格不建档、停用分类写入不复活不新建、transfer 与
update 方向不可判定防御不建档；修复项：loc_* 过滤排除作废笔
（include_voided 两态+五参逐一）与无 loc 过滤作废笔照常返回（既有回归）。
全部数据脱敏虚构；一律走 conftest 临时库，真实库零触碰。
"""

from __future__ import annotations

import asyncio
import json as _json
import uuid

import pytest

from ledger.errors import ConflictError
from ledger.models import (
    AccountCreate,
    CategoryCreate,
    CategoryUpdate,
    LocationInfo,
    TransactionCreate,
)
from ledger.services import accounts, categories, transactions as tx


def _mcp():
    from ledger.mcp_server import build_mcp

    return build_mcp()


def _call(mcp, tool, args):
    async def run():
        res = await mcp.call_tool(tool, args)
        return _json.loads(res[0].text)

    return asyncio.run(run())


def _call_err(mcp, tool, args) -> str:
    """MCP 面错误捕获（FastMCP 异常冒泡，宽捕获+语义子串断言稳态）。"""
    try:
        _call(mcp, tool, args)
    except Exception as exc:  # noqa: BLE001 — 测试面宽捕获
        return str(exc)
    raise AssertionError("expected error not raised")


def _account():
    return accounts.create_account(
        AccountCreate(name=f"账户{uuid.uuid4().hex[:8]}", type="cash"))


def _full_loc(city="北京", name="虚构咖啡店"):
    return LocationInfo(name=name, city=city, point="116.397428,39.90923",
                        poi_id="B0TESTPOI0001", adcode="110105",
                        type="餐饮服务;咖啡厅", biz="国贸", addr="虚构路 1 号")


def _expense_loc(loc):
    return tx.create(TransactionCreate(
        type="expense", amount="10.00", category_id="cat_exp_01",
        account_id=_account().id, location=loc))


# ---------- 主项：list_categories（MCP 查询工具·先查后选） ----------

def test_mcp_list_categories_full_direction_disabled():
    mcp = _mcp()
    full = _call(mcp, "list_categories", {})["categories"]
    assert len(full) == 14  # seed 预置 14 类全启用
    assert len([c for c in full if c["direction"] == "expense"]) == 9
    assert len([c for c in full if c["direction"] == "income"]) == 5
    only_exp = _call(mcp, "list_categories", {"direction": "expense"})["categories"]
    assert len(only_exp) == 9 and all(c["direction"] == "expense" for c in only_exp)
    only_inc = _call(mcp, "list_categories", {"direction": "income"})["categories"]
    assert len(only_inc) == 5 and all(c["direction"] == "income" for c in only_inc)
    # 第三态：停用分类默认不可见，include_disabled=True 可见
    categories.update_category("cat_exp_01", CategoryUpdate(enabled=False))
    after = _call(mcp, "list_categories", {})["categories"]
    assert len(after) == 13 and all(c["enabled"] for c in after)
    with_disabled = _call(
        mcp, "list_categories", {"include_disabled": True})["categories"]
    assert len(with_disabled) == 14
    dining = next(c for c in with_disabled if c["id"] == "cat_exp_01")
    assert dining["enabled"] is False


# ---------- 主项：写入面 get-or-create 动态建档 ----------

def test_write_new_category_name_auto_creates_level1():
    """建档→写入闭环：一级挂载+方向继承+非预置+sort=0；同向重名幂等命中。"""
    mcp = _mcp()
    out = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "12.50", "category": "体育健身",
        "account": _account().name})
    created = next(c for c in categories.list_categories() if c.name == "体育健身")
    assert created.parent_id is None            # 一律一级挂载（拍板③）
    assert created.direction == "expense"       # 方向继承交易方向（拍板③）
    assert created.is_preset is False           # 非预置（拍板④）
    assert created.sort == 0 and created.enabled is True
    assert not created.id.startswith("cat_exp")  # 预置 seed 前缀禁用（拍板④）
    assert out["category_id"] == created.id     # 建档→写入闭环（category_id 对齐）
    # 同名同向再次写入：幂等返回既有，零重复建档
    out2 = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "1.00", "category": "体育健身",
        "account": _account().name})
    assert out2["category_id"] == created.id
    assert len([c for c in categories.list_categories()
                if c.name == "体育健身"]) == 1


def test_same_direction_duplicate_name_conflict_unchanged():
    """R6 服务层重名约束未被 get-or-create 破坏：同向重名仍 ConflictError。"""
    categories.create_category(CategoryCreate(name="宠物用品", direction="expense"))
    with pytest.raises(ConflictError):
        categories.create_category(CategoryCreate(name="宠物用品", direction="expense"))
    # 跨方向同名仍合法（既有口径）
    assert categories.create_category(
        CategoryCreate(name="宠物用品", direction="income")) is not None


def test_uuid_direct_missing_strict_not_found_no_create():
    """UUID 直传未命中：严格报 category_not_found 零建档（拍板④）。"""
    mcp = _mcp()
    before = len(categories.list_categories())
    ghost = str(uuid.uuid4())
    err = _call_err(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "3.00", "category": ghost,
        "account": _account().name})
    assert "分类不存在" in err
    assert len(categories.list_categories()) == before


def test_disabled_category_write_reports_disabled_no_revive_no_create():
    """停用分类名称写入：命中后写路径报 category_disabled，不复活不新建。"""
    mcp = _mcp()
    categories.update_category("cat_exp_01", CategoryUpdate(enabled=False))
    before = len(categories.list_categories())
    err = _call_err(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "5.00", "category": "餐饮",
        "account": _account().name})
    assert "已停用" in err
    after = categories.list_categories()
    assert len(after) == before  # 不新建
    assert next(c for c in after if c.id == "cat_exp_01").enabled is False  # 不复活


def test_transfer_category_defense_no_create():
    """方向不可判定（transfer）不建档：不存在名报 not_found；存在名仍拒转账挂分类。"""
    mcp = _mcp()
    a = _account().name
    b = accounts.create_account(
        AccountCreate(name=f"账户{uuid.uuid4().hex[:8]}", type="cash")).name
    before = len(categories.list_categories())
    err = _call_err(mcp, "add_transaction", {
        "txn_type": "transfer", "amount": "1.00", "account": a,
        "counter_account": b, "category": "不存在分类XYZ"})
    assert "分类不存在" in err
    assert len(categories.list_categories()) == before
    err2 = _call_err(mcp, "add_transaction", {
        "txn_type": "transfer", "amount": "1.00", "account": a,
        "counter_account": b, "category": "餐饮"})
    assert "转账" in err2  # transfer_category_forbidden（既有行为零回归）
    assert len(categories.list_categories()) == before


def test_update_without_txn_type_new_name_no_create():
    """update 局部更新不传 txn_type（方向不可判定）传新分类名：只解析不建档。"""
    mcp = _mcp()
    a = _account()
    t = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "2.00", "category": "餐饮",
        "account": a.name})
    before = len(categories.list_categories())
    err = _call_err(mcp, "update_transaction", {
        "txn_id": t["id"], "category": "全新分类QQQ"})
    assert "分类不存在" in err
    assert len(categories.list_categories()) == before


# ---------- 修复项：loc_* 过滤排除作废笔 ----------

def test_loc_filter_excludes_voided_default_and_forced():
    """voided 笔带 loc 值不出现在 loc_* 过滤结果（include_voided 两态）。"""
    t_active = _expense_loc(_full_loc())
    t_voided = _expense_loc(_full_loc(name="作废咖啡店"))
    tx.void(t_voided.id)
    # 默认态（include_voided=False）：既有行为，作废笔不出现
    r0 = tx.list_transactions(loc_city="北京")
    assert r0.total == 1 and r0.items[0].id == t_active.id
    # 显式 include_voided=True：仍强制排除作废笔（新语义）
    r1 = tx.list_transactions(loc_city="北京", include_voided=True)
    assert r1.total == 1 and r1.items[0].id == t_active.id


def test_loc_filter_excludes_voided_all_five_params():
    """五参任一生效即强制排除作废笔（无论 include_voided 取值）。"""
    t_active = _expense_loc(_full_loc())
    t_voided = _expense_loc(_full_loc(name="作废店"))
    tx.void(t_voided.id)
    for filt in (dict(loc_city="北京"), dict(loc_type="餐饮服务;咖啡厅"),
                 dict(loc_biz="国贸"), dict(loc_poi_id="B0TESTPOI0001"),
                 dict(loc_keyword="虚构路")):
        for iv in (False, True):
            r = tx.list_transactions(include_voided=iv, **filt)
            assert r.total == 1 and r.items[0].id == t_active.id, (filt, iv)
    # 仅命中作废笔的关键词同样被强制排除
    assert tx.list_transactions(include_voided=True,
                                loc_keyword="作废店").total == 0
    assert tx.list_transactions(include_voided=False,
                                loc_keyword="作废店").total == 0


def test_include_voided_true_without_loc_filter_still_returns_voided():
    """既有语义回归：无 loc_* 过滤时 include_voided=True 作废笔照常返回。"""
    _expense_loc(_full_loc())
    t_voided = _expense_loc(_full_loc(name="回归作废店"))
    tx.void(t_voided.id)
    assert tx.list_transactions(include_voided=True).total == 2
    assert tx.list_transactions().total == 1
