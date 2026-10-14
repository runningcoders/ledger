"""交易服务测试：R1/R3/R4/R5 落位 + 列表筛选口径。全部数据脱敏虚构。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ledger.errors import ConflictError, LedgerError, NotFoundError
from ledger.models import TransactionCreate, TransactionUpdate
from ledger.services import accounts, categories, transactions as tx

EXP_DINING = "cat_exp_01"   # 餐饮（预置）
INC_WAGE = "cat_inc_01"     # 工资（预置）
EXP2 = "cat_exp_02"    # 交通（预置）


def _account(name=None, atype="cash", initial="0"):
    if name is None:
        name = f"账户{uuid.uuid4().hex[:8]}"  # 默认唯一名，避免 UNIQUE 冲突
    from ledger.models import AccountCreate
    return accounts.create_account(
        AccountCreate(name=name, type=atype, initial_balance=initial))


def _create(**kw):
    if "account_id" not in kw:
        kw["account_id"] = _account().id  # 惰性建户：显式传 account_id 时不建
    base = dict(type="expense", amount="12.50", category_id=EXP_DINING,
                note="午餐·虚构样例")
    base.update(kw)
    return tx.create(TransactionCreate(**base))


# ---------- R1 完整性 ----------

def test_create_expense_happy_path():
    out = _create()
    assert out.type == "expense" and out.status == "active"
    assert str(out.amount) == "12.50" and out.in_sum is True
    assert out.tags == []


def test_amount_must_be_positive():
    with pytest.raises(ValidationError):
        TransactionCreate(type="expense", amount="0", category_id=EXP_DINING,
                          account_id="a", )
    with pytest.raises(ValidationError):
        TransactionCreate(type="expense", amount="-3.00", category_id=EXP_DINING,
                          account_id="a")


def test_category_required_for_non_transfer():
    with pytest.raises(LedgerError) as ei:
        _create(category_id=None)
    assert ei.value.code == "category_required"


def test_category_direction_mismatch():
    with pytest.raises(LedgerError) as ei:
        _create(category_id=INC_WAGE)  # 支出走收入分类
    assert ei.value.code == "category_direction_mismatch"


def test_category_not_found():
    with pytest.raises(NotFoundError):
        _create(category_id="cat_nope")


def test_account_not_found():
    with pytest.raises(NotFoundError):
        _create(account_id="acct_nope")


def test_occurred_at_defaults_to_now_and_accepts_iso():
    a = _create()
    assert a.occurred_at is not None
    b = _create(occurred_at="2026-10-01T08:00:00")
    assert b.occurred_at.year == 2026 and b.occurred_at.month == 10


# ---------- R3 转账 ----------

def test_transfer_requires_counter_account():
    with pytest.raises(LedgerError) as ei:
        _create(type="transfer", category_id=None)
    assert ei.value.code == "transfer_counter_required"


def test_transfer_same_account_rejected():
    a = _account("借记卡A", "debit")
    with pytest.raises(LedgerError) as ei:
        _create(type="transfer", category_id=None, account_id=a.id,
                counter_account_id=a.id)
    assert ei.value.code == "transfer_same_account"


def test_transfer_category_forbidden():
    a, b = _account("卡A", "debit"), _account("卡B", "credit")
    with pytest.raises(LedgerError) as ei:
        _create(type="transfer", category_id=EXP_DINING, account_id=a.id,
                counter_account_id=b.id)
    assert ei.value.code == "transfer_category_forbidden"


def test_transfer_in_sum_forced_false():
    a, b = _account("卡A", "debit"), _account("卡B", "credit")
    out = _create(type="transfer", category_id=None, account_id=a.id,
                  counter_account_id=b.id, in_sum=True)  # 显式 true 也被强制
    assert out.in_sum is False and out.category_id is None


# ---------- R4 不计收支 ----------

def test_in_sum_false_still_moves_balance_but_not_overview():
    from ledger.services.stats import overview
    a = _account("钱包", "cash", "100.00")
    _create(amount="30.00", account_id=a.id, note="代垫·不计收支",
            occurred_at="2026-06-05T12:00:00")
    _create(amount="20.00", account_id=a.id, in_sum=False, note="信用卡还款·腾挪",
            occurred_at="2026-06-06T12:00:00")
    ov = overview("2026-06")
    assert str(ov.expense) == "30.00"   # R4：不计收支项不进统计
    bal = {x.name: x for x in accounts.list_accounts()}["钱包"]
    assert str(bal.balance) == "50.00"  # 但资金真实变动


# ---------- R5 作废 ----------

def test_void_then_update_conflict_and_double_void():
    out = _create()
    tx.void(out.id)
    with pytest.raises(ConflictError) as ei:
        tx.update(out.id, TransactionUpdate(note="改不了"))
    assert ei.value.code == "transaction_voided_immutable"
    with pytest.raises(ConflictError):
        tx.void(out.id)


def test_voided_excluded_by_default_and_visible_with_flag():
    out = _create(note="待作废")
    tx.void(out.id)
    assert tx.list_transactions().total == 0
    assert tx.list_transactions(include_voided=True).total == 1
    got = tx.get(out.id)
    assert got.status == "voided"


def test_no_physical_delete_path_exists():
    # R5 声明性自检：服务层不提供 delete 动作
    assert not hasattr(tx, "delete")


# ---------- 修改语义 ----------

def test_update_partial_merge_and_validation():
    out = _create()
    upd = tx.update(out.id, TransactionUpdate(amount="88.00", note="改后"))
    assert str(upd.amount) == "88.00" and upd.note == "改后"
    assert upd.category_id == EXP_DINING  # 未传字段保持


def test_update_switch_to_transfer_clears_category_and_forces_in_sum():
    out = _create()
    b = _account("卡B", "credit")
    upd = tx.update(out.id, TransactionUpdate(
        type="transfer", counter_account_id=b.id))
    assert upd.category_id is None and upd.in_sum is False
    assert upd.counter_account_id == b.id


def test_update_switch_off_transfer_clears_counter():
    a, b = _account("卡A", "debit"), _account("卡B", "credit")
    t = _create(type="transfer", category_id=None, account_id=a.id,
                counter_account_id=b.id)
    upd = tx.update(t.id, TransactionUpdate(
        type="expense", category_id=EXP_DINING))
    assert upd.counter_account_id is None and upd.type == "expense"


def test_update_tags_replace_whole():
    out = _create(tags=["旅行"])
    upd = tx.update(out.id, TransactionUpdate(tags=["报销待核", "旅行"]))
    assert sorted(t.name for t in upd.tags) == ["报销待核", "旅行"]  # 名称排序返回


# ---------- 查询口径（M3）----------

def test_list_filters_time_window_end_date_inclusive():
    _create(occurred_at="2026-10-01T10:00:00", note="当日第一笔")
    _create(occurred_at="2026-10-02T10:00:00")
    got = tx.list_transactions(start="2026-10-01", end="2026-10-01")
    assert got.total == 1  # end 日期按整日含端
    assert got.items[0].note == "当日第一笔"


def test_list_filter_category_includes_children():
    from ledger.models import CategoryCreate
    child = categories.create_category(
        CategoryCreate(name="早餐", parent_id=EXP_DINING, direction="expense"))
    _create(category_id=EXP_DINING, note="父类支出")
    _create(category_id=child.id, note="子类支出")
    got = tx.list_transactions(category_id=EXP_DINING)
    assert got.total == 2  # 含子分类


def test_list_filter_account_covers_both_sides_of_transfer():
    a, b = _account("卡A", "debit"), _account("卡B", "credit")
    _create(amount="5.00", category_id=EXP_DINING, account_id=a.id)
    _create(type="transfer", category_id=None, amount="9.00",
            account_id=a.id, counter_account_id=b.id)
    assert tx.list_transactions(account_id=a.id).total == 2
    assert tx.list_transactions(account_id=b.id).total == 1


def test_list_filter_tags_or_and_keyword_note_and_category():
    _create(tags=["旅行", "家庭"], note="机票·虚构")
    _create(tags=["报销待核"], note="办公用品")
    got = tx.list_transactions(tag=["旅行", "报销待核"])  # OR 语义
    assert got.total == 2
    assert tx.list_transactions(tag=["旅行", "报销待核"],
                                keyword="机票").total == 1
    assert tx.list_transactions(keyword="交通").total == 0  # 无交通分类流水
    _create(category_id=EXP2, note=None)
    assert tx.list_transactions(keyword="交通").total == 1  # 命中分类名


def test_list_amount_range_and_pagination():
    for i, amt in enumerate(["1.00", "5.00", "50.00", "500.00"]):
        _create(amount=amt, note=f"第{i}笔",
                occurred_at=f"2026-05-{10 + i:02d}T12:00:00")
    assert tx.list_transactions(min_amount="5", max_amount="50").total == 2
    page1 = tx.list_transactions(limit=2, offset=0)
    page2 = tx.list_transactions(limit=2, offset=2)
    assert page1.total == 4 and page2.total == 4
    assert len(page1.items) == 2 and len(page2.items) == 2
    # 时间倒序：最新在前
    assert page1.items[0].occurred_at >= page2.items[0].occurred_at


def test_get_not_found():
    with pytest.raises(NotFoundError):
        tx.get("no-such-id")

# ---------- R5 作废·带标签（void 子表外键回归） ----------

def test_void_tagged_transaction_succeeds_and_tags_traceable():
    """带标签交易可作废（DuckDB 父行 UPDATE 对尚存子行 FK 敏感的回归）；
    作废后标签原样保留——R5 作废可追溯语义。"""
    out = _create(tags=["餐饮补贴", "通勤"])
    assert len(out.tags) == 2
    voided = tx.void(out.id)
    assert voided.status == "voided"
    assert sorted(t.name for t in voided.tags) == ["通勤", "餐饮补贴"]  # 标签零丢失


def test_void_tagged_excluded_from_stats_and_default_list():
    """作废带标签交易后：默认列表/收支统计正确排除、余额对账正确排除。"""
    from ledger.services.stats import overview
    a = _account("现金钱包B", "cash", "100.00")
    t = _create(amount="30.00", account_id=a.id, tags=["餐饮补贴"],
                occurred_at="2026-06-05T12:00:00")
    tx.void(t.id)
    assert tx.list_transactions().total == 0                       # 默认排除
    assert tx.list_transactions(include_voided=True).total == 1    # 可查兜底
    got = tx.get(t.id)
    assert got.status == "voided" and [x.name for x in got.tags] == ["餐饮补贴"]
    ov = overview("2026-06")
    assert str(ov.expense) == "0.00"                               # 统计排除
    bal = {x.name: x for x in accounts.list_accounts()}["现金钱包B"]
    assert str(bal.balance) == "100.00"                            # 余额对账排除


def test_void_tagged_transfer_and_double_void_conflict():
    """带标签转账作废同样可用（转账无分类不挂标签限制，tags 合法）；
    重复作废 409 语义不变。"""
    a, b = _account("卡C", "debit"), _account("卡D", "credit")
    t = tx.create(TransactionCreate(type="transfer", amount="9.00",
                                    category_id=None, account_id=a.id,
                                    counter_account_id=b.id, tags=["还款"]))
    out = tx.void(t.id)
    assert out.status == "voided" and [x.name for x in out.tags] == ["还款"]
    with pytest.raises(ConflictError):
        tx.void(t.id)


def test_void_tagged_join_rows_restored_without_duplication():
    """回写不产生重复联接（PK 约束自证）：作废后联接行数与标签集不变。"""
    out = _create(tags=["旅行"])
    tx.void(out.id)
    from ledger.db import connect
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM transaction_tags WHERE transaction_id = ?",
            [out.id]).fetchone()[0]
    assert n == 1
    got = tx.get(out.id)
    assert [t.name for t in got.tags] == ["旅行"]
