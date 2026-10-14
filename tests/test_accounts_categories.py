"""账户余额派生 + 分类树约束测试（R6/R3/R5 口径落位）。全部数据脱敏虚构。"""

from __future__ import annotations

import uuid

import pytest

from ledger.errors import ConflictError, LedgerError, NotFoundError
from ledger.models import AccountCreate, AccountUpdate, CategoryCreate, CategoryUpdate
from ledger.models import TransactionCreate
from ledger.services import accounts, categories, transactions as tx

EXP = "cat_exp_01"


def _acct(name=None, atype="cash", initial="0"):
    if name is None:
        name = f"账户{uuid.uuid4().hex[:8]}"  # 默认唯一名，避免 UNIQUE 冲突
    return accounts.create_account(
        AccountCreate(name=name, type=atype, initial_balance=initial))


def _txn(**kw):
    if "account_id" not in kw:
        kw["account_id"] = _acct().id  # 惰性建户
    base = dict(type="expense", amount="10.00", category_id=EXP)
    base.update(kw)
    return tx.create(TransactionCreate(**base))


# ---------- 账户 ----------

def test_balance_derivation_full_matrix():
    a = _acct("钱包", "cash", "100.00")      # 初始 100
    b = _acct("借记卡", "debit", "200.00")   # 初始 200
    _txn(amount="30.00", account_id=a.id, occurred_at="2026-03-01T12:00:00")
    _txn(type="income", amount="50.00", category_id="cat_inc_01",
         account_id=a.id, occurred_at="2026-03-02T12:00:00")
    _txn(type="transfer", category_id=None, amount="20.00",
         account_id=a.id, counter_account_id=b.id, occurred_at="2026-03-03T12:00:00")
    _txn(amount="10.00", account_id=a.id, in_sum=False, occurred_at="2026-03-04T12:00:00")
    _txn(amount="5.00", account_id=b.id, occurred_at="2026-03-05T12:00:00")
    got = {x.name: x for x in accounts.list_accounts()}
    # 钱包：100-30+50-20(转出)-10(不计收支仍动账) = 90.00
    assert str(got["钱包"].balance) == "90.00"
    # 借记卡：200+20(转入)-5 = 215.00
    assert str(got["借记卡"].balance) == "215.00"


def test_voided_excluded_from_balance():
    a = _acct("钱包2", initial="50.00")
    t = _txn(amount="20.00", account_id=a.id, occurred_at="2026-03-10T12:00:00")
    tx.void(t.id)
    bal = {x.name: x for x in accounts.list_accounts()}["钱包2"]
    assert str(bal.balance) == "50.00"


def test_account_name_unique_conflict():
    _acct("唯一卡", "debit")
    with pytest.raises(ConflictError):
        _acct("唯一卡", "credit")


def test_account_update_rename_and_enable():
    a = _acct("旧名")
    upd = accounts.update_account(
        a.id, AccountUpdate(name="新名", enabled=False))
    assert upd.name == "新名" and upd.enabled is False


def test_account_rename_conflict():
    _acct("甲")
    b = _acct("乙")
    with pytest.raises(ConflictError):
        accounts.update_account(b.id, AccountUpdate(name="甲"))


# ---------- 分类（R6）----------

def test_two_level_tree_enforced():
    lvl1 = categories.create_category(
        CategoryCreate(name="宠物业", direction="expense"))
    lvl2 = categories.create_category(
        CategoryCreate(name="猫粮", parent_id=lvl1.id, direction="expense"))
    assert lvl2.parent_id == lvl1.id
    with pytest.raises(LedgerError) as ei:
        categories.create_category(
            CategoryCreate(name="罐头", parent_id=lvl2.id, direction="expense"))
    assert ei.value.code == "category_depth_exceeded"


def test_child_direction_inherited_and_mismatch_rejected():
    lvl1 = categories.create_category(
        CategoryCreate(name="学习", direction="income"))
    assert categories.create_category(
        CategoryCreate(name="书费", parent_id=lvl1.id, direction="income")
    ).direction == "income"
    with pytest.raises(LedgerError) as ei:
        categories.create_category(
            CategoryCreate(name="学费", parent_id=lvl1.id, direction="expense"))
    assert ei.value.code == "category_direction_mismatch"


def test_sibling_duplicate_name_rejected_including_root_level():
    categories.create_category(CategoryCreate(name="美妆", direction="expense"))
    with pytest.raises(ConflictError):
        categories.create_category(CategoryCreate(name="美妆", direction="expense"))
    # seed 的「其他」支出/收入双向均已存在 → 两个方向再建都冲突
    with pytest.raises(ConflictError):
        categories.create_category(CategoryCreate(name="其他", direction="expense"))
    with pytest.raises(ConflictError):
        categories.create_category(CategoryCreate(name="其他", direction="income"))
    # 跨方向同名合法：本测已建 expense「美妆」，income 侧同名可建
    ok = categories.create_category(CategoryCreate(name="美妆", direction="income"))
    assert ok is not None and ok.direction == "income"


def test_preset_14_rows_flagged_and_update_semantics():
    allc = categories.list_categories()
    presets = [c for c in allc if c.is_preset]
    assert len(presets) == 14
    dining = next(c for c in presets if c.id == "cat_exp_01")
    assert dining.enabled is True
    # 预置集可停用（R6：停用≠删除；无删除端点=禁删的落位形态）
    upd = categories.update_category(
        dining.id, CategoryUpdate(enabled=False, sort=99))
    assert upd.enabled is False and upd.sort == 99


def test_category_update_not_found():
    with pytest.raises(NotFoundError):
        categories.update_category("nope", CategoryUpdate(name="x"))


def test_category_direction_immutable_after_create():
    # direction 不在可改模型内（CategoryUpdate 无该字段）——字段级封禁即不可改
    assert "direction" not in CategoryUpdate.model_fields
