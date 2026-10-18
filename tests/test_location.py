"""地点信息 LocationInfo 测试（任务 01a14d06·AMan 拍板②修正）。

覆盖：校验（point 正则/长度上限）、三级空值语义、JSON 往返零丢失（point
GCJ-02 原值直存直出）、update location=None 语义、_ensure_columns 旧库升级
路径、list 5 过滤、summarize 4 地点维度+空值归组、MCP 结构体端到端、坏 JSON
防御回退、OpenAPI 契约（前端 gen:api 依赖）、REST 面创建/修改。全部数据脱敏虚构。
"""

from __future__ import annotations

import asyncio
import json as _json
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ledger.db import connect
from ledger.errors import LedgerError
from ledger.models import AccountCreate, LocationInfo, TransactionCreate, TransactionUpdate
from ledger.services import accounts, stats, transactions as tx


def _account():
    return accounts.create_account(
        AccountCreate(name=f"账户{uuid.uuid4().hex[:8]}", type="cash"))


def _expense(amount="10.00", loc=None, **kw):
    params: dict = dict(type="expense", amount=amount, category_id="cat_exp_01",
                        account_id=_account().id)
    if loc is not None:
        params["location"] = loc
    params.update(kw)
    return tx.create(TransactionCreate(**params))


def _full_loc(**over):
    base = dict(name="虚构咖啡店", city="北京", point="116.397428,39.90923",
                poi_id="B000A8UIN8", adcode="110105", type="餐饮服务;咖啡厅",
                biz="国贸", addr="虚构路 1 号", raw="高德POI片段·虚构")
    base.update(over)
    return LocationInfo(**base)


# ---------- 校验 ----------

def test_locationinfo_validation_point_and_length():
    assert _full_loc().point == "116.397428,39.90923"
    assert LocationInfo(point="-116.4,39.9").point == "-116.4,39.9"  # 西经负值
    for bad in ("116.4", "116.4,39.9,999", "116.4;,39.9", "abc,def", "116.4,"):
        with pytest.raises(ValidationError):
            LocationInfo(point=bad)
    with pytest.raises(ValidationError):
        LocationInfo(name="x" * 129)
    with pytest.raises(ValidationError):
        LocationInfo(addr="x" * 257)
    with pytest.raises(ValidationError):
        LocationInfo(poi_id="x" * 33)
    with pytest.raises(ValidationError):
        LocationInfo(adcode="x" * 17)
    assert LocationInfo(name="x" * 128, addr="x" * 256) is not None  # 上限内合法


# ---------- 三级空值语义 ----------

def test_three_level_null_semantics():
    # 一级：整体 None=无地点
    t1 = _expense()
    assert t1.location is None
    assert tx.get(t1.id).location is None
    # 二级：部分填充（仅 raw）
    t2 = _expense(loc=LocationInfo(raw="仅原始片段·虚构"))
    got2 = tx.get(t2.id)
    assert got2.location is not None and got2.location.raw == "仅原始片段·虚构"
    assert got2.location.city is None and got2.location.point is None
    # 三级：完整 9 键
    t3 = _expense(loc=_full_loc())
    got3 = tx.get(t3.id)
    assert got3.location == _full_loc()


def test_json_roundtrip_zero_loss():
    """point GCJ-02 原值直存直出：经 loc 列 JSON 串往返后逐键相等（零变换零丢失）。"""
    loc = _full_loc(point="-116.3974284567,39.9092312345")
    created = _expense(loc=loc)
    got = tx.get(created.id)
    assert got.location.point == "-116.3974284567,39.9092312345"
    assert got.location == loc  # 9 键全等
    with connect() as conn:  # 存储形态实证：单列 JSON 串原值
        stored = conn.execute("SELECT loc FROM transactions WHERE id = ?",
                              [created.id]).fetchone()[0]
    assert _json.loads(stored)["point"] == "-116.3974284567,39.9092312345"


# ---------- update 语义 ----------

def test_update_location_none_keeps():
    t = _expense(loc=_full_loc())
    out = tx.update(t.id, TransactionUpdate(note="改备注·不带地点"))
    assert out.note == "改备注·不带地点"
    assert out.location == _full_loc()  # location=None=不变更


def test_update_location_replaces():
    t = _expense(loc=_full_loc())
    new_loc = _full_loc(city="上海", name="虚构书店", point="121.473701,31.230416")
    out = tx.update(t.id, TransactionUpdate(location=new_loc))
    assert out.location.city == "上海" and out.location.name == "虚构书店"
    assert out.location.point == "121.473701,31.230416"


# ---------- 旧库升级路径 ----------

def _build_old_db(path):
    """按 loc 列出现前的旧 schema 建库插数（14 列 transactions）。"""
    import duckdb
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE categories (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,"
        " parent_id VARCHAR, direction VARCHAR NOT NULL, enabled BOOLEAN DEFAULT TRUE,"
        " is_preset BOOLEAN DEFAULT FALSE, sort INTEGER DEFAULT 0)")
    conn.execute(
        "CREATE TABLE accounts (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,"
        " type VARCHAR NOT NULL, currency VARCHAR DEFAULT 'CNY',"
        " initial_balance DECIMAL(18,2) DEFAULT 0, enabled BOOLEAN DEFAULT TRUE,"
        " created_at TIMESTAMP DEFAULT current_timestamp)")
    conn.execute(
        "CREATE TABLE transactions (id VARCHAR PRIMARY KEY, type VARCHAR NOT NULL,"
        " amount DECIMAL(18,2) NOT NULL, currency VARCHAR DEFAULT 'CNY',"
        " category_id VARCHAR, account_id VARCHAR NOT NULL,"
        " counter_account_id VARCHAR, occurred_at TIMESTAMP NOT NULL, note VARCHAR,"
        " in_sum BOOLEAN DEFAULT TRUE, status VARCHAR DEFAULT 'active',"
        " created_at TIMESTAMP DEFAULT current_timestamp,"
        " updated_at TIMESTAMP DEFAULT current_timestamp)")
    conn.execute("CREATE TABLE tags (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL UNIQUE)")
    conn.execute("CREATE TABLE transaction_tags (transaction_id VARCHAR NOT NULL,"
                 " tag_id VARCHAR NOT NULL, PRIMARY KEY (transaction_id, tag_id))")
    conn.execute("CREATE TABLE budgets (id VARCHAR PRIMARY KEY, period VARCHAR NOT NULL,"
                 " category_id VARCHAR, amount DECIMAL(18,2) NOT NULL,"
                 " created_at TIMESTAMP DEFAULT current_timestamp,"
                 " UNIQUE (period, category_id))")
    conn.execute("INSERT INTO categories VALUES ('cat_exp_01','餐饮',NULL,'expense',TRUE,TRUE,1)")
    conn.execute("INSERT INTO accounts VALUES ('acc_old','旧账户','cash','CNY',0,TRUE,current_timestamp)")
    conn.execute("INSERT INTO transactions (id,type,amount,category_id,account_id,occurred_at)"
                 " VALUES ('txn_old','expense',12.50,'cat_exp_01','acc_old','2026-10-01 12:00:00')")
    conn.close()


def test_ensure_columns_old_db_upgrade(tmp_ledger_db):
    _build_old_db(tmp_ledger_db)
    with connect() as conn:  # connect 自动补列（_ensure_columns）
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM duckdb_columns()"
            " WHERE schema_name='main' AND table_name='transactions'").fetchall()]
    assert cols.count("loc") == 1
    # 旧数据 NULL 语义
    old = tx.get("txn_old")
    assert old.location is None
    # 旧库上新写入带地点
    created = _expense(amount="8.00", loc=_full_loc(), account_id="acc_old")
    got = tx.get(created.id)
    assert got.location == _full_loc()
    # 重连幂等再验（列不重复、数据仍可读）
    with connect() as conn:
        cols2 = [r[0] for r in conn.execute(
            "SELECT column_name FROM duckdb_columns()"
            " WHERE schema_name='main' AND table_name='transactions'").fetchall()]
    assert cols2.count("loc") == 1
    assert tx.get(created.id).location == _full_loc()
    assert tx.get("txn_old").location is None


# ---------- list 5 过滤 ----------

def test_list_loc_filters_exact():
    _expense(loc=_full_loc())                      # 北京/餐饮服务;咖啡厅/国贸/B000A8UIN8
    _expense(loc=_full_loc(city="上海", type="购物服务", biz="陆家嘴",
                            poi_id="B001B8UIN9", adcode="310100"))  # 全键区分
    _expense()                                     # 无地点
    assert tx.list_transactions(loc_city="北京").total == 1
    assert tx.list_transactions(loc_type="餐饮服务;咖啡厅").total == 1
    assert tx.list_transactions(loc_biz="国贸").total == 1
    assert tx.list_transactions(loc_poi_id="B000A8UIN8").total == 1
    assert tx.list_transactions(loc_city="深圳").total == 0
    # 组合过滤：城市+商圈交集
    assert tx.list_transactions(loc_city="北京", loc_biz="国贸").total == 1
    assert tx.list_transactions(loc_city="北京", loc_biz="陆家嘴").total == 0
    # 未标注地点不命中精确过滤
    assert tx.list_transactions(loc_city="上海").total == 1


def test_list_loc_keyword_and_no_hit():
    _expense(loc=_full_loc())                                # name=虚构咖啡店 addr=虚构路 1 号
    _expense(loc=LocationInfo(name="虚构面馆", addr="虚构大道 2 号"))
    assert tx.list_transactions(loc_keyword="咖啡").total == 1   # 命中 name
    assert tx.list_transactions(loc_keyword="大道").total == 1   # 命中 addr
    assert tx.list_transactions(loc_keyword="虚构").total == 2   # 两者皆中
    assert tx.list_transactions(loc_keyword="火锅").total == 0   # 零命中
    # 与既有筛选组合不冲突
    assert tx.list_transactions(loc_keyword="咖啡", txn_type="expense").total == 1


# ---------- summarize 地点维度 ----------

def test_summarize_loc_dims_and_null_group():
    _expense(amount="10.00", loc=_full_loc())                     # 北京/110105/餐饮服务;咖啡厅/国贸
    _expense(amount="20.00", loc=_full_loc(city="上海", adcode="310100",
                                           type="购物服务", biz="陆家嘴"))
    _expense(amount="5.00")                                       # 无地点
    out = stats.summary(start="2026-01-01", end="2026-12-31", group_by="loc_city")
    by_key = {g.key: g for g in out.items}
    assert by_key["北京"].expense == Decimal("10.00")
    assert by_key["上海"].expense == Decimal("20.00")
    assert by_key["未标注地点"].expense == Decimal("5.00")  # NULL 统一归组
    for dim, key, amount in (("loc_adcode", "110105", "10.00"),
                             ("loc_type", "餐饮服务;咖啡厅", "10.00"),
                             ("loc_biz", "国贸", "10.00")):
        out2 = stats.summary(start="2026-01-01", end="2026-12-31", group_by=dim)
        hit = [g for g in out2.items if g.key == key]
        assert len(hit) == 1 and hit[0].expense == Decimal(amount)
    # 既有五维零回归冒烟（白名单扩展不影响）
    assert stats.summary(start="2026-01-01", end="2026-12-31",
                         group_by="category").items[0].key == "cat_exp_01"


def test_summarize_invalid_group_by_rejected():
    with pytest.raises(LedgerError) as ei:
        stats.summary(start="2026-01-01", end="2026-12-31", group_by="loc_foo")
    assert ei.value.code == "invalid_group_by"


# ---------- MCP 端到端（结构体参数经工具签名调用） ----------

def test_mcp_end_to_end_location_struct_param(tmp_ledger_db):
    from ledger.mcp_server import build_mcp
    mcp = build_mcp()
    loc = {"name": "虚构面馆", "city": "北京", "point": "116.397428,39.90923",
           "type": "餐饮服务", "addr": "虚构路 1 号"}

    async def run_add():
        res = await mcp.call_tool("add_transaction", {
            "txn_type": "expense", "amount": "25.00", "category": "餐饮",
            "location": loc})
        return _json.loads(res[0].text)

    async def run_list(params):
        res = await mcp.call_tool("list_transactions", params)
        return _json.loads(res[0].text)

    out = asyncio.run(run_add())
    assert out["location"]["name"] == "虚构面馆"
    assert out["location"]["point"] == "116.397428,39.90923"
    got = asyncio.run(run_list({"loc_city": "北京"}))
    assert got["total"] == 1 and got["items"][0]["location"]["name"] == "虚构面馆"
    got2 = asyncio.run(run_list({"loc_keyword": "面馆"}))
    assert got2["total"] == 1
    got3 = asyncio.run(run_list({"loc_city": "深圳"}))
    assert got3["total"] == 0


def test_corrupt_loc_json_defensive(tmp_ledger_db):
    """坏 JSON 防御回退：location=None 不阻断主流程（get 与列表双面）。"""
    t = _expense(loc=_full_loc())
    with connect() as conn:
        conn.execute("UPDATE transactions SET loc = '{bad json' WHERE id = ?", [t.id])
    assert tx.get(t.id).location is None
    lst = tx.list_transactions()
    assert lst.total == 1 and lst.items[0].location is None


# ---------- OpenAPI 契约与 REST 面 ----------

def test_openapi_contains_location_contract(tmp_ledger_db):
    from ledger.api import app
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "LocationInfo" in schemas
    assert set(schemas["LocationInfo"]["properties"].keys()) == {
        "name", "city", "point", "poi_id", "adcode", "type", "biz", "addr", "raw"}
    assert "location" in schemas["TransactionCreate"]["properties"]
    assert "location" in schemas["TransactionUpdate"]["properties"]
    assert "location" in schemas["TransactionOut"]["properties"]


def test_rest_create_update_with_location(tmp_ledger_db):
    from ledger.api import app
    client = TestClient(app)
    loc = {"name": "虚构咖啡店", "city": "北京", "point": "116.397428,39.90923"}
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "18.00", "category_id": "cat_exp_01",
        "account_id": _account().id, "location": loc})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert r.json()["location"]["city"] == "北京"
    # PATCH 不带 location → 不变更
    r2 = client.patch(f"/api/v1/transactions/{tid}", json={"note": "改备注"})
    assert r2.status_code == 200 and r2.json()["location"]["city"] == "北京"
    # PATCH 带 location → 覆盖
    r3 = client.patch(f"/api/v1/transactions/{tid}", json={
        "location": {"city": "上海", "name": "虚构书店"}})
    assert r3.json()["location"]["city"] == "上海"
    assert r3.json()["location"]["name"] == "虚构书店"


# ---------- REST 面地点 group_by（补单 01a14d84：pattern 扩 9 值接缝修复） ----------

def test_rest_summary_loc_group_dims(tmp_ledger_db):
    """REST GET /api/v1/stats/summary group_by=4 地点值：200 且分组正确，含 NULL 归组。"""
    from ledger.api import create_app

    client = TestClient(create_app())
    loc = {"name": "虚构咖啡店", "city": "北京", "point": "116.397428,39.90923",
           "adcode": "110105", "type": "餐饮服务;咖啡厅", "biz": "国贸"}
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "18.00", "category_id": "cat_exp_01",
        "account_id": _account().id, "location": loc})
    assert r.status_code == 201
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "7.00", "category_id": "cat_exp_01",
        "account_id": _account().id})  # 无地点 → 「未标注地点」NULL 归组
    assert r.status_code == 201

    for dim, key, amount in (("loc_city", "北京", "18.00"),
                             ("loc_adcode", "110105", "18.00"),
                             ("loc_type", "餐饮服务;咖啡厅", "18.00"),
                             ("loc_biz", "国贸", "18.00")):
        resp = client.get("/api/v1/stats/summary", params={
            "start": "2026-01-01", "end": "2026-12-31", "group_by": dim})
        assert resp.status_code == 200, (dim, resp.status_code)
        body = resp.json()
        assert body["group_by"] == dim
        by_key = {i["key"]: i["expense"] for i in body["items"]}
        assert by_key == {key: amount, "未标注地点": "7.00"}, (dim, by_key)

    # 「未标注地点」组：key 与 name 同为归组标签（COALESCE 双表达式同值）
    r = client.get("/api/v1/stats/summary", params={
        "start": "2026-01-01", "end": "2026-12-31", "group_by": "loc_city"})
    null_row = [i for i in r.json()["items"] if i["name"] == "未标注地点"]
    assert len(null_row) == 1
    assert null_row[0]["key"] == "未标注地点"
    assert null_row[0]["expense"] == "7.00"


def test_rest_summary_invalid_loc_group_by_rejected(tmp_ledger_db):
    """非法 group_by（含地点形态变体 loc_foo）仍被 REST pattern 422 拦截。"""
    from ledger.api import create_app

    client = TestClient(create_app())
    r = client.get("/api/v1/stats/summary", params={
        "start": "2026-01-01", "end": "2026-12-31", "group_by": "loc_foo"})
    assert r.status_code == 422
