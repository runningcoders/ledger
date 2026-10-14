import type { ReactNode } from 'react'
import type { Account, Category, Txn } from '../api/model'
import { amountColorClass, amountSign, thousands, kindOf } from '../lib/format'

interface Props {
  txn: Txn
  accounts: Account[]
  categories: Category[]
  /** 作废确认进行中（确认键 loading） */
  voiding: boolean
  onClose: () => void
  onEdit: () => void
  onVoid: () => void
}

const TYPE_LABEL: Record<string, string> = {
  expense: '支出',
  income: '收入',
  transfer: '转账',
}

function accountName(accounts: Account[], id?: string | null): string {
  if (!id) return '—'
  return accounts.find((a) => a.id === id)?.name ?? id
}

function categoryName(categories: Category[], id?: string | null): string {
  if (!id) return '—'
  const c = categories.find((x) => x.id === id)
  if (!c) return id
  if (c.parent_id) {
    const p = categories.find((x) => x.id === c.parent_id)
    return p ? `${p.name}·${c.name}` : c.name
  }
  return c.name
}

function fmtTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 流水详情抽屉（UI 规范 §3.4：字段表 + 编辑/作废；作废二次确认红色键） */
export default function TransactionDetail({
  txn,
  accounts,
  categories,
  voiding,
  onClose,
  onEdit,
  onVoid,
}: Props) {
  const voided = txn.status === 'voided'
  const isTransfer = txn.type === 'transfer'
  const kind = kindOf(txn.type, txn.in_sum)

  const rows: [string, ReactNode][] = [
    ['金额', <span key="amt" className={`tabular-nums font-medium ${amountColorClass(kind)}`}>{amountSign(kind)}{thousands(txn.amount)}</span>],
    ['类型', <span key="t">{TYPE_LABEL[txn.type] ?? txn.type}</span>],
    ['分类', <span key="c">{categoryName(categories, txn.category_id)}</span>],
    ['账户', <span key="a">{accountName(accounts, txn.account_id)}</span>],
    ...(isTransfer
      ? [['对方账户', <span key="ca">{accountName(accounts, txn.counter_account_id)}</span>] as [string, ReactNode]]
      : []),
    ['时间', <span key="at">{fmtTime(txn.occurred_at)}</span>],
    ['计入收支', <span key="is">{txn.in_sum ? '是' : '否'}</span>],
    ['标签', <span key="tg" className="flex flex-wrap gap-1">{(txn.tags ?? []).length > 0 ? (txn.tags ?? []).map((t) => <span key={t.id} className="badge badge-ghost badge-sm">{t.name}</span>) : '—'}</span>],
    ['备注', <span key="nt">{txn.note ?? '—'}</span>],
    ['创建时间', <span key="ct">{fmtTime(txn.created_at)}</span>],
  ]

  return (
    <div className="grid gap-4" data-testid="txn-detail">
      {/* 标题区：分类名 + 金额（语义色大字）+ 类型徽标 */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold text-lg">{categoryName(categories, txn.category_id)}</h3>
          <p className={`text-2xl tabular-nums ${amountColorClass(kind)}`}>
            {amountSign(kind)}{thousands(txn.amount)}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button className="btn btn-ghost btn-sm btn-circle" onClick={onClose} aria-label="关闭">✕</button>
          {isTransfer && <span className="badge badge-ghost badge-sm">转账</span>}
          {!txn.in_sum && !isTransfer && <span className="badge badge-ghost badge-sm">不计收支</span>}
          {voided && <span className="badge badge-error badge-sm">已作废</span>}
        </div>
      </div>

      {/* 作废态横幅 + 全字段只读灰化（D3） */}
      {voided && (
        <div className="alert alert-warning text-sm">
          已作废：该笔不再参与统计，流水保留可追溯（可在「含作废」筛选中查看）。
        </div>
      )}

      {/* 明细字段表 */}
      <div className={`grid gap-2 text-sm ${voided ? 'opacity-60 line-through decoration-base-content/40' : ''}`}>
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-start justify-between gap-4 border-b border-base-200 pb-1">
            <span className="opacity-60 shrink-0">{k}</span>
            <span className="text-right break-all">{v}</span>
          </div>
        ))}
      </div>

      {/* 操作区：作废态无编辑/作废按钮（D3） */}
      {!voided && (
        <div className="flex justify-end gap-2">
          <button className="btn btn-ghost" onClick={onEdit}>编辑</button>
          <button className="btn btn-error btn-outline" disabled={voiding} onClick={onVoid}>
            {voiding && <span className="loading loading-spinner loading-xs" />}
            作废
          </button>
        </div>
      )}
    </div>
  )
}
