"""总管只读模式测试：发现机制 / 按库隔离 / REST 入口分流 / 写凭证通道 / 聚合。

口径来源：TL 评估报告 01a14009（评估面 a-h）+ AMan 拍板 5 项（01a14409 落码单）。
隐私红线：全部脱敏虚构 uid（TESTUSER*/SMOKEUSER* 形态）+ 临时库 + HOME 重定向，
零真实平台 UID 零真实账本触碰（conftest 双保险）。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import ledger.db as db
from ledger.db import CredentialError, LedgerError
from ledger.services.discovery import discover_user_ids

_REPO_ROOT = Path(__file__).resolve().parent.parent

UID_A = "TESTUSER0000000001A"
UID_B = "TESTUSER0000000002B"


def _cred(user_id: str, **extra) -> str:
    payload: dict = {"v": 1, "user_id": user_id, **extra}
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


@pytest.fixture()
def manager_mode(monkeypatch, tmp_path):
    """总管模式态：delenv LEDGER_CREDENTIAL + HOME 重定向临时根（真实库零风险）。"""
    monkeypatch.delenv("LEDGER_CREDENTIAL", raising=False)
    monkeypatch.setattr(db, "_resolved_user_id", None)
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _seed_user_db(uid: str, expense_amount: str = "10.00") -> str:
    """在指定 uid 库中建 schema+seed 并落一笔支出（直接走服务层显式 uid）。

    返回 (txn_id, account_id, category_id)：供 REST 写用例按真实字段构造请求体
    （总管写路径按凭证 uid 路由到对应库，虚构 id 会被该库强校验 404——防错写语义）。
    """
    from ledger.models import AccountCreate, CategoryCreate, TransactionCreate
    from ledger.services import accounts, categories, transactions as tx

    acct = accounts.create_account(
        AccountCreate(name=f"账户{uid[-4:]}", type="cash"), uid=uid)
    cat = categories.create_category(
        CategoryCreate(name=f"分类{uid[-4:]}", direction="expense"), uid=uid)
    out = tx.create(
        TransactionCreate(type="expense", amount=expense_amount,
                          category_id=cat.id, account_id=acct.id,
                          occurred_at="2026-06-02T12:00:00"),
        uid=uid,
    )
    return out.id, acct.id, cat.id


def _client():
    from fastapi.testclient import TestClient

    from ledger.api import create_app

    return TestClient(create_app())


# ── 发现机制（评估面 b）────────────────────────────────────────────


def test_discover_anchor_is_marker_file(tmp_path):
    root = tmp_path / "users"
    for uid in (UID_A, UID_B):
        (root / uid / "personal").mkdir(parents=True)
        (root / uid / "personal" / "ledger.duckdb").write_bytes(b"x")
    (root / "PLAINDIR").mkdir()  # 无 marker 的目录=平台工作区形态，不得误判
    assert discover_user_ids(root) == sorted([UID_A, UID_B])


def test_discover_uid_whitelist_filters(tmp_path):
    root = tmp_path / "users"
    for uid in ("a/b", "..", ".hidden", UID_A):
        (root / uid / "personal").mkdir(parents=True, exist_ok=True)
        (root / uid / "personal" / "ledger.duckdb").write_bytes(b"x")
    assert discover_user_ids(root) == [UID_A]  # 白名单外（含路径注入形态）全过滤


def test_discover_empty_root_returns_empty_no_side_effects(tmp_path):
    root = tmp_path / "not-exist"
    assert discover_user_ids(root) == []
    assert not root.exists() and not (tmp_path / "users").exists()  # 零 mkdir 副作用


# ── db 层显式 uid（评估面 e）────────────────────────────────────────


def test_db_path_explicit_uid_ignores_ledger_db_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LEDGER_DB_PATH", str(tmp_path / "explicit.duckdb"))
    expected = tmp_path / ".ai_orz" / "data" / "users" / UID_A / "personal" / "ledger.duckdb"
    assert db.db_path(UID_A) == expected  # 显式 uid：env 不参与（防多库被引到同一文件）


def test_db_path_explicit_uid_whitelist_enforced():
    with pytest.raises(LedgerError) as ei:
        db.db_path("../etc")
    assert ei.value.code == "invalid_uid"


def test_connect_explicit_uid_isolates_databases(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    txn_a, _, _ = _seed_user_db(UID_A, "11.00")
    txn_b, _, _ = _seed_user_db(UID_B, "22.00")
    # 互相不可见（每用户独立库文件，隔离性）
    from ledger.services import transactions as tx

    assert [t.id for t in tx.list_transactions(uid=UID_A).items] == [txn_a]
    assert [t.id for t in tx.list_transactions(uid=UID_B).items] == [txn_b]


def test_schema_ready_b_scheme_per_database(monkeypatch, tmp_path):
    """方案 B 口径：同一进程先后连接两个全新库，各自完成首启建库（14 分类 seed）。"""
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    for uid in (UID_A, UID_B):
        with db.connect(uid) as conn:
            n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        assert n == 14  # schema.sql 幂等 seed 每库各自生效（无 Once 状态串库）


# ── REST 入口分流 + 写凭证通道（评估面 a/c，拍板①③④）──────────────


def test_manager_write_without_header_401(manager_mode):
    _seed_user_db(UID_A)
    r = _client().post("/api/v1/transactions", json={
        "type": "expense", "amount": "5.00", "category_id": "c", "account_id": "a"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "credential_missing"


def test_manager_write_bad_credential_401(manager_mode):
    _seed_user_db(UID_A)
    client = _client()
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "5.00", "category_id": "c", "account_id": "a"},
        headers={"X-Ledger-Credential": "not-base64**"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "base64_invalid"
    bad_json = base64.b64encode(b"not-json").decode("ascii")
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "5.00", "category_id": "c", "account_id": "a"},
        headers={"X-Ledger-Credential": bad_json})
    assert r.status_code == 401 and r.json()["error"]["code"] == "json_invalid"


def test_manager_write_routes_to_credential_uid(manager_mode):
    """写目标=凭证解析 uid（防贴他人凭证错写）：A 凭证写入 A 库，B 库零污染；
    虚构账户 id 被 A 库强校验拒绝（404，写路径防错写语义实证）。"""
    _, acct_a, cat_a = _seed_user_db(UID_A)   # A 库 seed（取真实字段供写请求体）
    _seed_user_db(UID_B)                       # B 库 seed
    client = _client()
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "5.00", "category_id": cat_a,
        "account_id": acct_a},
        headers={"X-Ledger-Credential": _cred(UID_A)})
    assert r.status_code == 201
    from ledger.services import transactions as tx

    assert tx.list_transactions(uid=UID_A).total == 2   # seed 1 笔+新写 1 笔
    assert tx.list_transactions(uid=UID_B).total == 1   # B 库零污染
    # 防错写：A 库存在的字段贴 B 凭证→路由到 B 库→B 库无此分类/账户→404（不落 A 库）
    r = client.post("/api/v1/transactions", json={
        "type": "expense", "amount": "5.00", "category_id": cat_a,
        "account_id": acct_a},
        headers={"X-Ledger-Credential": _cred(UID_B)})
    assert r.status_code == 404


def test_single_user_mode_write_without_header_ok(tmp_ledger_db):
    """单用户模式：写端点不要求请求头（现行为逐字节不变，评估面 c）。"""
    r = _client().get("/api/health")
    assert r.status_code == 200 and r.json()["mode"] == "single"
    r = _client().post("/api/v1/categories", json={
        "name": "单用户分类", "direction": "expense"})
    assert r.status_code == 201


def test_single_user_mode_uid_param_ignored(tmp_ledger_db):
    """单用户模式：读端点 uid 查询参数忽略（向后兼容零破坏，评估面 d）。"""
    r = _client().get("/api/v1/transactions", params={"uid": UID_A})
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}  # 读的是本库（临时库），非 UID_A 库


# ── 读聚合与切换（评估面 d，拍板②按用户切换）────────────────────────


def test_manager_read_aggregation_and_uid_switch(manager_mode):
    _seed_user_db(UID_A, "10.00")
    _seed_user_db(UID_B, "30.00")
    client = _client()

    r = client.get("/api/v1/users")
    assert r.status_code == 200
    assert r.json() == {"items": [UID_A, UID_B]}  # 字典序稳定

    r = client.get("/api/v1/transactions", params={"uid": UID_A})
    assert r.status_code == 200 and r.json()["total"] == 1  # 切换器：锁定单库

    r = client.get("/api/v1/transactions")  # 缺省=聚合全部
    assert r.status_code == 200 and r.json()["total"] == 2

    r = client.get("/api/v1/overview", params={"month": "2026-06"})
    body = r.json()
    assert str(body["income"]) == "0.00" and str(body["expense"]) == "40.00"  # 聚合求和
    assert str(body["balance"]) == "-40.00"

    r = client.get("/api/v1/accounts")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()}
    assert names == {"现金", "账户001A", "账户002B"}  # 两库拼接（各含 seed 现金+自建账户）

    r = client.get("/api/v1/stats/summary", params={
        "start": "2026-06-01", "end": "2026-06-30", "group_by": "category"})
    assert r.status_code == 200 and len(r.json()["items"]) == 2


def test_manager_read_empty_registry_empty_aggregation(manager_mode):
    """空集=返回空聚合非报错（评估面 b 容错口径）。"""
    client = _client()
    r = client.get("/api/v1/transactions")
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}
    r = client.get("/api/v1/overview", params={"month": "2026-06"})
    assert r.status_code == 200 and str(r.json()["expense"]) == "0.00"


def test_manager_read_invalid_or_missing_uid_rejected(manager_mode):
    client = _client()
    r = client.get("/api/v1/transactions", params={"uid": "../etc"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "invalid_uid"
    r = client.get("/api/v1/transactions", params={"uid": "NOSUCHUSER0000000001X"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "user_not_found"


def test_manager_health_and_single_user_health(manager_mode, tmp_path):
    r = _client().get("/api/health")
    assert r.json()["mode"] == "manager" and r.json()["user"] is None
    # 切回单用户：bootstrap 装配后 user 直显
    os.environ["LEDGER_CREDENTIAL"] = _cred(UID_A)
    try:
        db.bootstrap_credential()
        r = _client().get("/api/health")
        assert r.json()["mode"] == "single" and r.json()["user"] == UID_A
    finally:
        os.environ.pop("LEDGER_CREDENTIAL", None)
        db._resolved_user_id = None


# ── 入口分流 subprocess 实证（评估面 a：serve 放行 / MCP 零豁免）────


def test_serve_without_credential_boots_manager(monkeypatch, tmp_path):
    """serve 入口：LEDGER_CREDENTIAL 未设 → 总管模式放行（/api/health mode=manager）。"""
    port = 8873
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LEDGER_HOST": "127.0.0.1",
        "LEDGER_PORT": str(port),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "ledger", "serve"],
        env=env, cwd=_REPO_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        health = None
        for _ in range(60):
            if proc.poll() is not None:
                raise AssertionError(f"serve 提前退出：exit={proc.returncode}")
            time.sleep(0.3)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2
                ) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:
                continue
        assert health is not None and health["mode"] == "manager"
        # 总管模式写端点 401（端到端）
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/categories",
            data=json.dumps({"name": "x", "direction": "expense"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=3)
            raise AssertionError("总管模式无凭证写应被拒绝")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_mcp_stdio_without_credential_still_fail_fast(monkeypatch, tmp_path):
    """MCP 分支零豁免回归：无凭证 exit=2（与既有 multiuser 套件同口径，双入口分流定谳）。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LEDGER_DB_PATH": str(tmp_path / "unused.duckdb"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "ledger"],
        env=env, capture_output=True, text=True, timeout=30, cwd=_REPO_ROOT,
    )
    assert proc.returncode == 2
    assert "env_missing" in proc.stderr
