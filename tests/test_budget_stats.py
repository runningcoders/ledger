"""预算三态 + 统计口径测试（R3/R4/R5/R7 落位）。全部数据脱敏虚构。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ledger.errors import ConflictError, LedgerError
from ledger.models import (BudgetSetItem, BudgetSetRequest, TransactionCreate)
from ledger.services import accounts, budget, stats, transactions as tx

EXP = "cat_exp_01"   # 餐饮
EXP2 = "cat_exp_02"  # 交通
INC = "cat_inc_01"   # 工资


def _acct(name=None, initial="0"):
    from ledger.models import AccountCreate
    if name is None:
        name = f"账户{uuid.uuid4().hex[:8]}"  # 默认唯一名
    return accounts.create_account(
        AccountCreate(name=name, type="cash", initial_balance=initial))


def _txn(**kw):
    if "account_id" not in kw:
        kw["account_id"] = _acct().id  # 惰性建户
    base = dict(type="expense", amount="10.00", category_id=EXP)
    base.update(kw)
    return tx.create(TransactionCreate(**base))


@pytest.fixture()
def fixed_account():
    """function 级固定账户（每用例独立临时库，禁止跨用例缓存实体 ID）。"""
    return _acct("预算统计专用账户")


# ---------- 预算（R7 三态 + 替换式 PUT）----------

def test_budget_set_and_status_three_states(fixed_account):
    acct = fixed_account
    tx.create(TransactionCreate(type="expense", amount="90.00", category_id=EXP,
                                account_id=acct.id, occurred_at="2026-10-05T12:00:00"))
    out = budget.set_budgets(BudgetSetRequest(
        period="2026-10", items=[BudgetSetItem(category_id=None, amount="100.00"),
                                 BudgetSetItem(category_id=EXP, amount="100.00")]))
    lines = {l.category_id: l for l in out.lines}
    assert str(lines[None].used) == "90.00"
    assert lines[None].status == "near_limit"      # 90%：临近超支
    assert str(lines[EXP].used) == "90.00" and lines[EXP].status == "near_limit"
    assert str(lines[None].remaining) == "10.00"


def test_budget_over_state_and_negative_remaining(fixed_account):
    acct = fixed_account
    tx.create(TransactionCreate(type="expense", amount="120.00", category_id=EXP2,
                                account_id=acct.id, occurred_at="2026-10-06T12:00:00"))
    out = budget.set_budgets(BudgetSetRequest(
        period="2026-10", items=[BudgetSetItem(category_id=EXP2, amount="100.00")]))
    line = out.lines[0]
    assert line.status == "over"
    assert str(line.remaining) == "-20.00"  # 显示超出额（UI 呈现口径）


def test_budget_normal_state(fixed_account):
    acct = fixed_account
    tx.create(TransactionCreate(type="expense", amount="10.00", category_id=EXP,
                                account_id=acct.id, occurred_at="2026-10-07T12:00:00"))
    out = budget.set_budgets(BudgetSetRequest(
        period="2026-10", items=[BudgetSetItem(category_id=EXP, amount="100.00")]))
    assert out.lines[0].status == "normal"


def test_budget_includes_children_and_excludes_transfer_voided(fixed_account):
    acct = fixed_account
    from ledger.models import CategoryCreate
    from ledger.services import categories
    child = categories.create_category(
        CategoryCreate(name="打车", parent_id=EXP2, direction="expense"))
    tx.create(TransactionCreate(type="expense", amount="40.00", category_id=EXP2,
                                account_id=acct.id, occurred_at="2026-10-08T12:00:00"))
    tx.create(TransactionCreate(type="expense", amount="25.00", category_id=child.id,
                                account_id=acct.id, occurred_at="2026-10-09T12:00:00"))
    t_transfer = tx.create(TransactionCreate(
        type="transfer", category_id=None, amount="100.00",
        account_id=acct.id, counter_account_id=_acct("另一个卡").id,
        occurred_at="2026-10-10T12:00:00"))
    t_void = tx.create(TransactionCreate(type="expense", amount="200.00",
                                         category_id=EXP2, account_id=acct.id,
                                         occurred_at="2026-10-11T12:00:00"))
    tx.void(t_void.id)
    out = budget.set_budgets(BudgetSetRequest(
        period="2026-10", items=[BudgetSetItem(category_id=EXP2, amount="70.00")]))
    line = out.lines[0]
    assert str(line.used) == "65.00"       # 子级计入、转账/作废排除
    assert line.status == "near_limit"     # 65/70 ≈ 93% ≥ 80% 临近超支阈值


def test_budget_put_replaces_whole_set(fixed_account):
    acct = fixed_account
    budget.set_budgets(BudgetSetRequest(
        period="2026-11", items=[BudgetSetItem(category_id=None, amount="100.00"),
                                 BudgetSetItem(category_id=EXP, amount="50.00")]))
    out = budget.set_budgets(BudgetSetRequest(
        period="2026-11", items=[BudgetSetItem(category_id=EXP2, amount="30.00")]))
    ids = {l.category_id for l in out.lines}
    assert ids == {EXP2}  # 整体替换：旧总预算与餐饮预算消失


def test_budget_validation():
    # period 格式在 pydantic 构造期即拦截（契约层校验前置；服务层 _check_period 为纵深防御）
    with pytest.raises(ValidationError):
        BudgetSetRequest(period="2026/10", items=[BudgetSetItem(amount="10.00")])
    with pytest.raises(ConflictError):
        budget.set_budgets(BudgetSetRequest(
            period="2026-10", items=[BudgetSetItem(category_id=None, amount="1.00"),
                                     BudgetSetItem(category_id=None, amount="2.00")]))
    with pytest.raises(ConflictError):
        budget.set_budgets(BudgetSetRequest(
            period="2026-10", items=[BudgetSetItem(category_id=EXP, amount="1.00"),
                                     BudgetSetItem(category_id=EXP, amount="2.00")]))
    with pytest.raises(LedgerError):
        budget.set_budgets(BudgetSetRequest(
            period="2026-10", items=[BudgetSetItem(category_id="nope", amount="1.00")]))
    with pytest.raises(Exception):  # amount>0（pydantic）
        BudgetSetItem(amount="0")


def test_budget_default_period_is_current_month():
    out = budget.budget_status(None)
    assert len(out.period) == 7 and out.lines == []


# ---------- 统计（R3/R4/R5 口径）----------

def test_overview_excludes_transfer_in_sum_false_and_voided(fixed_account):
    acct = fixed_account
    tx.create(TransactionCreate(type="income", amount="300.00", category_id=INC,
                                account_id=acct.id, occurred_at="2026-08-01T12:00:00"))
    tx.create(TransactionCreate(type="expense", amount="80.00", category_id=EXP,
                                account_id=acct.id, occurred_at="2026-08-02T12:00:00"))
    tx.create(TransactionCreate(type="expense", amount="70.00", category_id=EXP2,
                                account_id=acct.id, in_sum=False,
                                occurred_at="2026-08-03T12:00:00"))  # R4 排除
    tx.create(TransactionCreate(type="transfer", category_id=None, amount="60.00",
                                account_id=acct.id,
                                counter_account_id=_acct("另一张卡2").id,
                                occurred_at="2026-08-04T12:00:00"))  # R3 排除
    t = tx.create(TransactionCreate(type="expense", amount="40.00", category_id=EXP,
                                    account_id=acct.id,
                                    occurred_at="2026-08-05T12:00:00"))
    tx.void(t.id)  # R5 排除（40 不进统计）
    ov = stats.overview("2026-08")
    assert str(ov.income) == "300.00"
    assert str(ov.expense) == "80.00"
    assert str(ov.balance) == "220.00"  # 结余=收-支（非账户余额）


def test_summary_group_by_category_and_direction(fixed_account):
    acct = fixed_account
    tx.create(TransactionCreate(type="expense", amount="30.00", category_id=EXP,
                                account_id=acct.id, occurred_at="2026-07-02T12:00:00"))
    tx.create(TransactionCreate(type="expense", amount="20.00", category_id=EXP2,
                                account_id=acct.id, occurred_at="2026-07-03T12:00:00"))
    tx.create(TransactionCreate(type="income", amount="100.00", category_id=INC,
                                account_id=acct.id, occurred_at="2026-07-04T12:00:00"))
    out = stats.summary(start="2026-07-01", end="2026-07-31", group_by="category")
    items = {i.key: i for i in out.items}
    assert str(items[EXP].expense) == "30.00" and str(items[EXP].income) == "0.00"
    assert str(items[EXP].net) == "-30.00"
    assert str(items[INC].income) == "100.00"
    assert str(items[INC].expense) == "0.00"


def test_summary_group_by_day_and_account_and_tag(fixed_account):
    acct = fixed_account
    tx.create(TransactionCreate(type="expense", amount="10.00", category_id=EXP,
                                account_id=acct.id, tags=["出差"],
                                occurred_at="2026-09-10T12:00:00"))
    tx.create(TransactionCreate(type="expense", amount="15.00", category_id=EXP2,
                                account_id=acct.id, tags=["出差", "通勤"],
                                occurred_at="2026-09-10T18:00:00"))
    by_day = stats.summary(start="2026-09-01", end="2026-09-30", group_by="day")
    assert len(by_day.items) == 1 and str(by_day.items[0].expense) == "25.00"
    by_acct = stats.summary(start="2026-09-01", end="2026-09-30", group_by="account")
    assert by_acct.items[0].key == acct.id
    by_tag = stats.summary(start="2026-09-01", end="2026-09-30", group_by="tag")
    tags = {i.name: str(i.expense) for i in by_tag.items}
    assert tags == {"出差": "25.00", "通勤": "15.00"}  # 多标签计入多分组


def test_summary_invalid_group_by():
    with pytest.raises(LedgerError):
        stats.summary(start="2026-09-01", end="2026-09-30", group_by="hack")
