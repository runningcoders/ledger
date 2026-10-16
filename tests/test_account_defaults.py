"""账户机制改造测试（AMan 拍板案 B，任务 01a14375）：默认账户 seed + 写入面
get-or-create + 查询面空集 + REST 强校验回归。

隐私红线：账户名/数据全部脱敏虚构样例，测试仅触碰每用例临时库文件，零真实账本。
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

from ledger.errors import ConflictError, LedgerError, NotFoundError
from ledger.models import AccountCreate, AccountUpdate, TransactionCreate
from ledger.services import accounts, stats, transactions as tx
from ledger.services.accounts import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_ACCOUNT_NAME,
    ensure_default_account,
)

EXP_FOOD = "cat_exp_01"    # 餐饮（预置）
EXP_TRANS = "cat_exp_02"   # 交通（预置）


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _txn(**kw):
    base = dict(type="expense", amount="12.50", category_id=EXP_FOOD,
                note="样例·脱敏虚构")
    base.update(kw)
    return tx.create(TransactionCreate(**base))


def _call(mcp, name, args):
    """call_tool 取数适配：venv mcp SDK（官方 1.x）返回 Sequence[ContentBlock]，
    JSON 位于 TextContent.text；兼容 (content, structured) 元组形态。"""
    out = mcp.call_tool(name, args)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    if isinstance(out, tuple) and len(out) == 2:
        out = out[1]
    import json

    if isinstance(out, list) and out and hasattr(out[0], "text"):
        return json.loads(out[0].text)
    return out


# ---------- a：默认账户（schema.sql seed + 幂等兜底）----------

def test_default_account_seeded_on_fresh_db():
    accs = accounts.list_accounts()
    cash = [a for a in accs if a.name == DEFAULT_ACCOUNT_NAME]
    assert len(cash) == 1
    assert cash[0].id == DEFAULT_ACCOUNT_ID and cash[0].type == "cash"
    assert Decimal(cash[0].balance) == 0


def test_ensure_default_account_idempotent():
    from ledger.db import connect

    with connect() as conn:
        assert ensure_default_account(conn) == DEFAULT_ACCOUNT_ID
        assert ensure_default_account(conn) == DEFAULT_ACCOUNT_ID
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


# ---------- b/c：写入面 get-or-create ----------

def test_resolve_or_create_virtual_and_idempotent():
    name = _uniq("card")
    aid1 = accounts.resolve_or_create_account_id(name)
    aid2 = accounts.resolve_or_create_account_id(name)  # UNIQUE 重名即命中幂等
    assert aid1 == aid2
    row = next(a for a in accounts.list_accounts() if a.id == aid1)
    assert row.type == "virtual" and row.enabled is True


def test_disabled_account_hit_not_revived():
    name = _uniq("frozen")
    acc = accounts.create_account(AccountCreate(name=name, type="debit"))
    accounts.update_account(acc.id, AccountUpdate(enabled=False))
    assert accounts.resolve_or_create_account_id(name) == acc.id  # 不复活
    with pytest.raises(LedgerError) as ei:
        _txn(account_id=acc.id)
    assert ei.value.code == "account_disabled"


def test_transfer_counter_auto_created_double_side_balance():
    from ledger.mcp_server import _account_write_ref

    name = _uniq("bank")
    counter_id = _account_write_ref(name)  # 转账对方账户自动建档（virtual）
    out = _txn(type="transfer", amount="100.00", category_id=None,
               account_id=DEFAULT_ACCOUNT_ID, counter_account_id=counter_id)
    assert out.counter_account_id == counter_id
    row = next(a for a in accounts.list_accounts() if a.id == counter_id)
    assert row.type == "virtual"
    cash = next(a for a in accounts.list_accounts() if a.id == DEFAULT_ACCOUNT_ID)
    assert Decimal(cash.balance) == Decimal("-100.00")   # 转出
    assert Decimal(row.balance) == Decimal("100.00")    # 转入


def test_summary_group_by_account_includes_new_account():
    from ledger.mcp_server import _account_write_ref

    name = _uniq("wallet")
    aid = _account_write_ref(name)
    _txn(amount="30.00", category_id=EXP_TRANS, account_id=aid)
    out = stats.summary(start="2000-01-01", end="2100-01-01", group_by="account")
    hit = [g for g in out.items if g.key == aid or g.name == name]
    assert len(hit) == 1 and hit[0].expense == Decimal("30.00")


# ---------- MCP 入口：M1 缺省→默认账户 / M3 不存在账户→空集 ----------

def test_mcp_m1_without_account_uses_default():
    from ledger.mcp_server import build_mcp

    out = _call(build_mcp(), "add_transaction",
                {"txn_type": "expense", "amount": "3.50",
                 "category": "交通", "note": "样例·脱敏虚构"})
    assert out["account_id"] == DEFAULT_ACCOUNT_ID
    cash = next(a for a in accounts.list_accounts() if a.id == DEFAULT_ACCOUNT_ID)
    assert Decimal(cash.balance) == Decimal("-3.50")


def test_mcp_m1_explicit_new_account_auto_creates():
    """回归堵口（冒烟实证缺陷修复）：M1 显式指定不存在账户名→get-or-create 自动建档。"""
    from ledger.mcp_server import build_mcp

    name = _uniq("smokewallet")
    out = _call(build_mcp(), "add_transaction",
                {"txn_type": "expense", "amount": "30.00", "account": name,
                 "category": "购物", "note": "样例·脱敏虚构"})
    row = next(a for a in accounts.list_accounts() if a.name == name)
    assert out["account_id"] == row.id and row.type == "virtual"


def test_mcp_m3_unknown_account_returns_empty_set():
    from ledger.mcp_server import build_mcp

    out = _call(build_mcp(), "list_transactions",
                {"account": "nonexistent_account_xyz"})
    assert out == {"items": [], "total": 0}


# ---------- REST 面强校验零回归（拍板案 B⑤）----------

def test_rest_strict_semantics_not_regressed():
    with pytest.raises(NotFoundError) as ei:
        accounts.resolve_account_id("nonexistent_account_xyz")
    assert ei.value.code == "account_not_found"
    with pytest.raises(ConflictError):  # 默认账户已 seed → REST 重名冲突仍报
        accounts.create_account(AccountCreate(name=DEFAULT_ACCOUNT_NAME, type="cash"))
