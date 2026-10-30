"""引用判别顺序修复 + 受保护归档 + MCP 回执入参快照（缺陷修复批次 01a18c71）。

三项同批交付覆盖：
① Fix#1 判别顺序＝「先按主键精确查 → 未命中再按名称 → 双未命中才允许建档」
   （分类 resolve_category_id / resolve_or_create_category_id；账户
   resolve_account_id / resolve_or_create_account_id / find_account_id）；
   四例守卫：预置 seed 主键直通不建档 / 不存在 UUID 仍严格不建档 / 不存在名称
   仍自动建档（B1 行为保留）/ 停用分类传 ID 报 disabled 不建档；
② C 案受保护归档：预置集 / 有子分类 / 有流水（含作废笔）/ 有预算 四类拒绝 +
   零引用孤儿分类成功移除并返回快照 + REST DELETE 契约；
③ _received 回执：传了回显原值、没传为 None、既有字段零变更。
全部数据脱敏虚构；一律走 conftest 临时库，真实库零触碰。
"""

from __future__ import annotations

import asyncio
import json as _json
import uuid

import pytest

from ledger.errors import ConflictError, NotFoundError
from ledger.models import AccountCreate, CategoryCreate, CategoryUpdate, TransactionCreate
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
    try:
        _call(mcp, tool, args)
    except Exception as exc:  # noqa: BLE001 — 测试面宽捕获
        return str(exc)
    raise AssertionError("expected error not raised")


def _account():
    return accounts.create_account(
        AccountCreate(name=f"账户{uuid.uuid4().hex[:8]}", type="cash"))


# ---------- ① Fix#1：分类判别顺序 ----------

def test_preset_seed_primary_key_resolves_without_creating():
    """预置主键 cat_inc_01（非 UUID 形态）直通命中，零自动建档。

    回归锚点：修复前该字面量被当「分类名称」→ 自动建档 name='cat_inc_01' 的
    垃圾分类（污染分类 6bdc4552-145a-4862-9c2d-8cdde3884c62）。
    """
    mcp = _mcp()
    before = categories.list_categories()
    out = _call(mcp, "add_transaction", {
        "txn_type": "income", "amount": "32238.84", "category": "cat_inc_01",
        "account": _account().name})
    assert out["category_id"] == "cat_inc_01"
    after = categories.list_categories()
    assert len(after) == len(before)                       # 零新建
    assert not any(c.name == "cat_inc_01" for c in after)  # 无同名垃圾分类
    assert next(c for c in after if c.id == "cat_inc_01").name == "工资"


def test_preset_seed_primary_key_expense_also_direct():
    """支出侧同样直通（cat_exp_01 / 餐饮），防只修一侧。"""
    assert categories.resolve_or_create_category_id("cat_exp_01", "expense") == "cat_exp_01"
    assert not any(c.name == "cat_exp_01" for c in categories.list_categories())


def test_nonexistent_uuid_still_strict_no_create():
    """不存在的 UUID 仍严格报错、零建档（拍板④零回归）。"""
    ghost = str(uuid.uuid4())
    before = len(categories.list_categories())
    with pytest.raises(NotFoundError) as ei:
        categories.resolve_or_create_category_id(ghost, "expense")
    assert ei.value.code == "category_not_found"
    assert len(categories.list_categories()) == before


def test_nonexistent_name_still_auto_creates_b1():
    """不存在的名称仍走自动建档（B1 行为保留）；ID 不用预置前缀。"""
    name = f"宠物{uuid.uuid4().hex[:6]}"
    cid = categories.resolve_or_create_category_id(name, "expense")
    created = next(c for c in categories.list_categories() if c.id == cid)
    assert created.name == name and created.direction == "expense"
    assert created.parent_id is None and created.is_preset is False
    assert not cid.startswith("cat_exp")


def test_disabled_preset_primary_key_reports_disabled_no_create():
    """停用分类传 ID：解析命中后由写路径报 category_disabled，零建档不复活。"""
    mcp = _mcp()
    categories.update_category("cat_inc_01", CategoryUpdate(enabled=False))
    before = len(categories.list_categories())
    err = _call_err(mcp, "add_transaction", {
        "txn_type": "income", "amount": "1.00", "category": "cat_inc_01",
        "account": _account().name})
    assert "已停用" in err
    after = categories.list_categories()
    assert len(after) == before
    assert next(c for c in after if c.id == "cat_inc_01").enabled is False


# ---------- ① Fix#1：账户判别顺序（同族潜在缺陷） ----------

def test_default_account_seed_primary_key_no_ghost_account():
    """acc_default_cash 直通命中现金账户，零幽灵虚拟账户（同族潜在缺陷）。"""
    mcp = _mcp()
    before = accounts.list_accounts()
    out = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "1.00", "category": "cat_exp_01",
        "account": "acc_default_cash"})
    assert out["account_id"] == "acc_default_cash"
    after = accounts.list_accounts()
    assert len(after) == len(before)
    assert not any(a.name == "acc_default_cash" for a in after)


def test_account_uuid_strict_and_name_creates():
    """账户：UUID 未命中严格报错不建档；名称未命中自动建档（virtual）。"""
    with pytest.raises(NotFoundError) as ei:
        accounts.resolve_or_create_account_id(str(uuid.uuid4()))
    assert ei.value.code == "account_not_found"
    name = f"卡{uuid.uuid4().hex[:8]}"
    aid = accounts.resolve_or_create_account_id(name)
    assert next(a for a in accounts.list_accounts() if a.id == aid).type == "virtual"


def test_find_account_id_prefers_primary_key():
    """查询面只读解析：主键与名称都命中，且不存在返回 None（非报错）。"""
    assert accounts.find_account_id("acc_default_cash") == "acc_default_cash"
    assert accounts.find_account_id("现金") == "acc_default_cash"
    assert accounts.find_account_id("nonexistent_account_xyz") is None


# ---------- ② C 案：受保护归档 ----------

def _new_cat(name=None):
    return categories.create_category(CategoryCreate(
        name=name or f"归档候选{uuid.uuid4().hex[:6]}", direction="expense"))


def test_archive_rejects_preset():
    with pytest.raises(ConflictError) as ei:
        categories.archive_category("cat_exp_01")
    assert ei.value.code == "category_preset_protected"
    assert next(c for c in categories.list_categories() if c.id == "cat_exp_01")


def test_archive_rejects_with_children():
    parent = _new_cat()
    categories.create_category(CategoryCreate(
        name=f"子{uuid.uuid4().hex[:6]}", parent_id=parent.id, direction="expense"))
    with pytest.raises(ConflictError) as ei:
        categories.archive_category(parent.id)
    assert ei.value.code == "category_has_children"
    assert next(c for c in categories.list_categories() if c.id == parent.id)


def test_archive_rejects_in_use_by_transaction_including_voided():
    """流水引用含作废笔：作废仍持外键，一律拒。"""
    cat = _new_cat()
    t = tx.create(TransactionCreate(
        type="expense", amount="9.00", category_id=cat.id, account_id=_account().id))
    with pytest.raises(ConflictError) as ei:
        categories.archive_category(cat.id)
    assert ei.value.code == "category_in_use"
    tx.void(t.id)
    with pytest.raises(ConflictError) as ei2:   # 作废笔同样拒
        categories.archive_category(cat.id)
    assert ei2.value.code == "category_in_use"


def test_archive_rejects_in_use_by_budget(tmp_ledger_db):
    """第三条引用面：budgets.category_id（常被遗漏，DELETE 会撞外键）。"""
    from ledger.db import connect

    cat = _new_cat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO budgets (id, period, category_id, amount) VALUES (?, ?, ?, ?)",
            [str(uuid.uuid4()), "2026-10", cat.id, 100])
    with pytest.raises(ConflictError) as ei:
        categories.archive_category(cat.id)
    assert ei.value.code == "category_in_use"
    assert next(c for c in categories.list_categories() if c.id == cat.id)


def test_archive_succeeds_for_orphan_and_returns_snapshot():
    cat = _new_cat("孤儿分类ABC")
    snap = categories.archive_category(cat.id)
    assert snap.id == cat.id and snap.name == "孤儿分类ABC"
    assert snap.direction == "expense" and snap.is_preset is False
    assert not any(c.id == cat.id for c in categories.list_categories())


def test_archive_not_found():
    with pytest.raises(NotFoundError) as ei:
        categories.archive_category(str(uuid.uuid4()))
    assert ei.value.code == "category_not_found"


def test_rest_delete_category_contract(tmp_ledger_db):
    """REST DELETE 契约：孤儿 200（返快照）→ 重复 404；预置 409。"""
    from fastapi.testclient import TestClient

    from ledger.api import create_app

    client = TestClient(create_app())
    cat = _new_cat("REST归档候选")
    r = client.delete(f"/api/v1/categories/{cat.id}")
    assert r.status_code == 200 and r.json()["id"] == cat.id
    assert client.delete(f"/api/v1/categories/{cat.id}").status_code == 404
    r2 = client.delete("/api/v1/categories/cat_exp_02")
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "category_preset_protected"


def test_openapi_contains_delete_categories(tmp_ledger_db):
    from fastapi.testclient import TestClient

    from ledger.api import create_app

    spec = TestClient(create_app()).get("/openapi.json").json()
    assert "delete" in spec["paths"]["/api/v1/categories/{category_id}"]


# ---------- ③ _received 回执入参快照 ----------

def test_received_echoes_raw_arguments():
    mcp = _mcp()
    out = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "18.00", "category": "cat_exp_01",
        "account": _account().name,
        "occurred_at": "2026-10-27T09:15:00",
        "tags": ["对账标签"],
        "location": {"name": "虚构咖啡店", "city": "北京"}})
    rec = out["_received"]
    assert rec["occurred_at"] == "2026-10-27T09:15:00"
    assert rec["tags"] == ["对账标签"]
    assert rec["location"]["city"] == "北京"
    assert out["occurred_at"].startswith("2026-10-27")   # 既有字段零变更


def test_received_null_when_absent():
    mcp = _mcp()
    out = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "1.00", "category": "cat_exp_01",
        "account": _account().name})
    rec = out["_received"]
    assert rec["occurred_at"] is None and rec["location"] is None
    assert rec["tags"] is None
    assert {"id", "type", "amount", "occurred_at", "location"} <= set(out)


def test_received_on_update_transaction():
    mcp = _mcp()
    a = _account()
    t = _call(mcp, "add_transaction", {
        "txn_type": "expense", "amount": "2.00", "category": "cat_exp_01",
        "account": a.name})
    out = _call(mcp, "update_transaction", {
        "txn_id": t["id"], "occurred_at": "2026-10-28T08:00:00",
        "location": {"city": "上海"}})
    assert out["_received"]["occurred_at"] == "2026-10-28T08:00:00"
    assert out["_received"]["location"]["city"] == "上海"
    assert out["_received"]["tags"] is None
