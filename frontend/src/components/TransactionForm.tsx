import { useState } from 'react'
import type { Account, Category, Tag, Txn } from '../api/model'
import type { TxnCreate, TxnUpdate } from '../api/model'
import { fromDatetimeLocal, toDatetimeLocal } from '../lib/datetime'

export interface TxnFormResult {
  payload: TxnCreate | TxnUpdate
}

interface Props {
  /** 编辑态传入既有流水（回填），新建态传 null */
  editing: Txn | null
  accounts: Account[]
  categories: Category[]
  tags: Tag[]
  submitting: boolean
  onCancel: () => void
  onSubmit: (payload: TxnCreate | TxnUpdate) => void
}

type TxnType = 'expense' | 'income' | 'transfer'

const TYPE_TABS: { key: TxnType; label: string }[] = [
  { key: 'expense', label: '支出' },
  { key: 'income', label: '收入' },
  { key: 'transfer', label: '转账' },
]

/** 记一笔表单（UI 规范 §3.3，新建/编辑双态复用；校验 R1 全行内即时报错） */
export default function TransactionForm({
  editing,
  accounts,
  categories,
  tags,
  submitting,
  onCancel,
  onSubmit,
}: Props) {
  const [type, setType] = useState<TxnType>((editing?.type as TxnType) ?? 'expense')
  const [amount, setAmount] = useState(editing ? editing.amount : '')
  const [categoryId, setCategoryId] = useState<string>(editing?.category_id ?? '')
  const [accountId, setAccountId] = useState<string>(editing?.account_id ?? '')
  const [counterAccountId, setCounterAccountId] = useState<string>(
    editing?.counter_account_id ?? '',
  )
  const [occurredAt, setOccurredAt] = useState<string>(
    toDatetimeLocal(editing?.occurred_at ?? new Date().toISOString()),
  )
  const [note, setNote] = useState(editing?.note ?? '')
  const [selectedTags, setSelectedTags] = useState<string[]>(
    (editing?.tags ?? []).map((t) => t.name),
  )
  const [newTagName, setNewTagName] = useState('')
  const [inSum, setInSum] = useState(editing ? editing.in_sum : true)

  // 行内校验错误（R1：即时报错，不用浏览器原生弹窗）
  const [errors, setErrors] = useState<Record<string, string>>({})

  const isTransfer = type === 'transfer'
  // 支出类型展示支出类分类、收入类型展示收入类分类（转账无分类，R3）
  const direction = type === 'income' ? 'income' : 'expense'
  const visibleCategories = categories.filter(
    (c) => !c.parent_id && c.direction === direction && c.enabled,
  )
  const subOf = (parent: string) =>
    categories.filter((c) => c.parent_id === parent && c.enabled)

  const toggleTag = (name: string) => {
    setSelectedTags((names) =>
      names.includes(name) ? names.filter((x) => x !== name) : [...names, name],
    )
  }

  /** 输入即挂标签（待提交）：后端在交易保存时按名称 get-or-create（无独立建标签端点） */
  const addTag = () => {
    const name = newTagName.trim()
    if (name === '') return
    setSelectedTags((names) => (names.includes(name) ? names : [...names, name]))
    setNewTagName('')
  }

  const validate = (): boolean => {
    const errs: Record<string, string> = {}
    const amt = Number(amount)
    if (!amount || Number.isNaN(amt) || amt <= 0) errs.amount = '金额需大于 0'
    if (!isTransfer && !categoryId) errs.categoryId = '请选择分类'
    if (!accountId) errs.accountId = '请选择账户'
    if (isTransfer && !counterAccountId) errs.counterAccountId = '请选择对方账户'
    if (isTransfer && counterAccountId && counterAccountId === accountId)
      errs.counterAccountId = '对方账户不能与转出账户相同'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const submit = () => {
    if (submitting) return // 防重复提交
    if (!validate()) return

    const base = {
      type,
      amount,
      account_id: accountId,
      counter_account_id: isTransfer ? counterAccountId : null,
      category_id: isTransfer ? null : categoryId,
      occurred_at: occurredAt ? fromDatetimeLocal(occurredAt) : undefined,
      note: note.trim() === '' ? undefined : note.trim(),
      in_sum: isTransfer ? false : inSum, // 转账强制不计收支（R3，后端双保险）
      tags: selectedTags,
    }

    if (editing) {
      onSubmit(base as TxnUpdate)
    } else {
      onSubmit(base as TxnCreate)
    }
  }

  const errText = (k: string) =>
    errors[k] ? <span className="text-error text-xs">{errors[k]}</span> : null

  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
      {/* 1. 类型切换（切换不清空已填金额与备注） */}
      <div role="tablist" className="tabs tabs-box">
        {TYPE_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            className={`tab ${type === t.key ? 'tab-active' : ''}`}
            onClick={() => setType(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 2. 金额（大号输入框，焦点自动落此，R1 校验 >0） */}
      <label className="form-control">
        <div className="label"><span className="label-text">金额</span></div>
        <input
          autoFocus
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0"
          className="input input-bordered w-full text-2xl tabular-nums"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="0.00"
        />
        {errText('amount')}
      </label>

      {/* 3. 分类（转账隐藏，R3） */}
      {!isTransfer && (
        <label className="form-control">
          <div className="label"><span className="label-text">分类</span></div>
          <div className="grid grid-cols-2 gap-2">
            <select
              className="select select-bordered w-full"
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
            >
              <option value="">选择一级分类</option>
              {visibleCategories.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            {categoryId && subOf(categoryId).length > 0 && (
              <select
                className="select select-bordered w-full"
                value={categoryId}
                onChange={(e) => setCategoryId(e.target.value)}
              >
                <option value={categoryId}>仅一级</option>
                {subOf(categoryId).map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            )}
          </div>
          {errText('categoryId')}
        </label>
      )}

      {/* 4. 账户 */}
      <label className="form-control">
        <div className="label"><span className="label-text">账户</span></div>
        <select
          className="select select-bordered w-full"
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
        >
          <option value="">选择账户</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        {errText('accountId')}
      </label>

      {/* 5. 转账附加字段：对方账户（必填且≠转出账户） */}
      {isTransfer && (
        <label className="form-control">
          <div className="label"><span className="label-text">对方账户</span></div>
          <select
            className="select select-bordered w-full"
            value={counterAccountId}
            onChange={(e) => setCounterAccountId(e.target.value)}
          >
            <option value="">选择对方账户</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          {errText('counterAccountId')}
        </label>
      )}

      {/* 6. 时间：默认当下可改 + 快捷项 今天/昨天 */}
      <label className="form-control">
        <div className="label">
          <span className="label-text">时间</span>
          <span className="flex gap-1">
            <button
              type="button"
              className="btn btn-ghost btn-xs"
              onClick={() => {
                const d = new Date()
                setOccurredAt(toDatetimeLocal(d.toISOString()))
              }}
            >
              今天
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-xs"
              onClick={() => {
                const d = new Date(Date.now() - 24 * 3600 * 1000)
                setOccurredAt(toDatetimeLocal(d.toISOString()))
              }}
            >
              昨天
            </button>
          </span>
        </div>
        <input
          type="datetime-local"
          className="input input-bordered w-full"
          value={occurredAt}
          onChange={(e) => setOccurredAt(e.target.value)}
        />
      </label>

      {/* 7. 备注 */}
      <label className="form-control">
        <div className="label"><span className="label-text">备注（选填）</span></div>
        <input
          type="text"
          className="input input-bordered w-full"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="备注原文"
        />
      </label>

      {/* 8. 标签：chips 点选 + 输入即挂（提交时后端按名称 get-or-create） */}
      <div className="form-control">
        <div className="label"><span className="label-text">标签（选填）</span></div>
        <div className="flex flex-wrap gap-1">
          {tags.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`badge ${selectedTags.includes(t.name) ? 'badge-primary' : 'badge-ghost'}`}
              onClick={() => toggleTag(t.name)}
            >
              {t.name}
            </button>
          ))}
          {selectedTags
            .filter((n) => !tags.some((t) => t.name === n))
            .map((n) => (
              <button
                key={n}
                type="button"
                className="badge badge-primary"
                onClick={() => toggleTag(n)}
              >
                {n}
              </button>
            ))}
        </div>
        <div className="flex gap-2 mt-2">
          <input
            type="text"
            className="input input-bordered input-sm w-full"
            value={newTagName}
            onChange={(e) => setNewTagName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addTag()
              }
            }}
            placeholder="输入后回车添加，保存时创建"
          />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={newTagName.trim() === ''}
            onClick={addTag}
          >
            添加
          </button>
        </div>
      </div>

      {/* 9. 不计入收支开关（默认关；开启时显示口径说明，R4） */}
      {!isTransfer && (
        <label className="label cursor-pointer justify-start gap-3">
          <input
            type="checkbox"
            className="toggle toggle-sm"
            checked={!inSum}
            onChange={(e) => setInSum(!e.target.checked)}
          />
          <span className="label-text">不计入收支</span>
          {!inSum && (
            <span className="label-text-alt text-warning">
              该笔不进入收支统计与概览，仅影响账户余额
            </span>
          )}
        </label>
      )}

      {/* 10. 提交区（提交中 loading 防重复） */}
      <div className="modal-action">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          取消
        </button>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting && <span className="loading loading-spinner loading-xs" />}
          {editing ? '保存修改' : '保存'}
        </button>
      </div>
    </form>
  )
}
