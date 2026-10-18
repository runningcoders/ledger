import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  LedgerPreset,
  ListQuery,
  StatsSummary,
  Txn,
  TxnCreate,
  TxnUpdate,
} from '../api/model'
import { api } from '../api/model'
import { currentMonth } from '../useLedgerData'
import type { LedgerData } from '../useLedgerData'
import { dayKey, groupTitle, hhmm } from '../lib/datetime'
import { amountColorClass, amountSign, kindOf, thousands } from '../lib/format'
import TransactionForm from '../components/TransactionForm'
import TransactionDetail from '../components/TransactionDetail'

type TxnType = 'expense' | 'income' | 'transfer'

interface Filters {
  type: '' | TxnType
  categoryId: string
  accountId: string
  start: string
  end: string
  keyword: string
  minAmount: string
  maxAmount: string
  includeVoided: boolean
}

const EMPTY_FILTERS: Filters = {
  type: '',
  categoryId: '',
  accountId: '',
  start: '',
  end: '',
  keyword: '',
  minAmount: '',
  maxAmount: '',
  includeVoided: false,
}

const PAGE_SIZE = 50

function errText(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

function fmtDate(v: string): string {
  return v // date input 原生即 'YYYY-MM-DD'
}

interface DayGroup {
  day: string
  income: string
  expense: string
  items: Txn[]
}

interface Props {
  /** 全局基础数据（App 状态提升统一拉取，跨页共享） */
  data: LedgerData
  loading: boolean
  error: string | null
  reload: () => void
  /** 统计页/账户页跳转预置筛选（一次性消费） */
  preset: LedgerPreset | null
  onPresetConsumed: () => void
}

export default function LedgerPage({
  data,
  loading,
  error,
  reload,
  preset,
  onPresetConsumed,
}: Props) {
  const { accounts, categories, tags } = data
  const overview = data.overview

  // —— 筛选（B1：六件套即时生效 + chips + 重置）——
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [items, setItems] = useState<Txn[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)
  const listSeq = useRef(0)
  // B2 当日收支小计：直接消费后端 stats/summary day 粒度（前端零金额算术，铁律）
  const [dayStats, setDayStats] = useState<Map<string, StatsSummary>>(new Map())
  // 提交/作废后强制列表重拉（offset 已为 0 时 setOffset 不触发 effect）
  const [listTick, setListTick] = useState(0)

  // —— 弹窗态 ——
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Txn | null>(null)
  const [detail, setDetail] = useState<Txn | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [voiding, setVoiding] = useState(false)
  const [voidConfirm, setVoidConfirm] = useState(false)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const notify = (kind: 'ok' | 'err', text: string) => {
    setToast({ kind, text })
    window.setTimeout(() => setToast(null), 2500)
  }

  // 统计页扇区/日历格、账户页账户卡跳转 → 预置筛选一次性消费（UI 规范 §5 动线）
  useEffect(() => {
    if (!preset) return
    setFilters((f) => ({
      ...f,
      categoryId: preset.categoryId ?? f.categoryId,
      accountId: preset.accountId ?? f.accountId,
      start: preset.start ?? f.start,
      end: preset.end ?? f.end,
    }))
    onPresetConsumed()
  }, [preset, onPresetConsumed])

  const queryParams = useMemo(() => {
    const q: Partial<ListQuery> = {}
    if (filters.type) q.type = filters.type
    if (filters.categoryId) q.category_id = filters.categoryId
    if (filters.accountId) q.account_id = filters.accountId
    if (filters.start) q.start = `${fmtDate(filters.start)} 00:00:00`
    if (filters.end) q.end = `${fmtDate(filters.end)} 23:59:59`
    if (filters.keyword) q.keyword = filters.keyword
    if (filters.minAmount) q.min_amount = filters.minAmount
    if (filters.maxAmount) q.max_amount = filters.maxAmount
    if (filters.includeVoided) q.include_voided = true
    return q
  }, [filters])

  // 筛选变化即重拉（即时生效），重置 offset
  useEffect(() => {
    setOffset(0)
  }, [queryParams])

  useEffect(() => {
    let alive = true
    const seq = ++listSeq.current
    setListLoading(true)
    setListError(null)
    api
      .listTxns({ ...queryParams, limit: PAGE_SIZE, offset })
      .then((data) => {
        if (!alive || seq !== listSeq.current) return
        setItems((prev) => (offset === 0 ? data.items : [...prev, ...data.items]))
        setTotal(data.total)
        setListLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive || seq !== listSeq.current) return
        setListError(errText(e))
        setListLoading(false)
      })
    return () => {
      alive = false
    }
  }, [queryParams, offset, listTick])

  // 当日小计随列表窗口取数（P1 精确口径：按筛选条件过滤的当日收支小计，
  // stats/summary 已扩展 category_id/account_id 筛选参数——后端单 01a13cfb 落地）
  useEffect(() => {
    if (items.length === 0) return
    const days = items.map((t) => dayKey(t.occurred_at)).filter((d) => d !== '')
    if (days.length === 0) return
    const sorted = [...days].sort()
    let alive = true
    api
      .dailyStats(`${sorted[0]} 00:00:00`, `${sorted[sorted.length - 1]} 23:59:59`, {
        ...(filters.categoryId ? { category_id: filters.categoryId } : {}),
        ...(filters.accountId ? { account_id: filters.accountId } : {}),
      })
      .then((out) => {
        if (!alive) return
        setDayStats(new Map(out.items.map((s) => [s.key ?? '', s])))
      })
      .catch(() => {
        // 小计拉取失败不阻塞列表主体（头部显示 0.00）
        if (alive) setDayStats(new Map())
      })
    return () => {
      alive = false
    }
  }, [items, filters])

  // —— 按日分组（B2：分组头带当日收支小计）——
  const groups: DayGroup[] = useMemo(() => {
    const map = new Map<string, DayGroup>()
    for (const t of items) {
      const day = dayKey(t.occurred_at)
      let g = map.get(day)
      if (!g) {
        g = { day, income: '0.00', expense: '0.00', items: [] }
        map.set(day, g)
      }
      g.items.push(t)
    }
    return Array.from(map.values())
  }, [items])

  const hasFilters =
    filters.type !== '' ||
    filters.categoryId !== '' ||
    filters.accountId !== '' ||
    filters.start !== '' ||
    filters.end !== '' ||
    filters.keyword !== '' ||
    filters.minAmount !== '' ||
    filters.maxAmount !== '' ||
    filters.includeVoided

  // —— 动作 ——
  const openEdit = (t: Txn) => {
    setDetail(null)
    setEditing(t)
    setFormOpen(true)
  }

  const closeForm = () => {
    if (!submitting) {
      setFormOpen(false)
      setEditing(null)
    }
  }

  const submitForm = async (payload: unknown) => {
    setSubmitting(true)
    try {
      const isEdit = editing !== null
      if (isEdit) {
        await api.updateTxn(editing!.id, payload as TxnUpdate)
      } else {
        await api.createTxn(payload as TxnCreate)
      }
      setFormOpen(false)
      setEditing(null)
      setDetail(null)
      notify('ok', isEdit ? '已保存' : '已记一笔')
      reload()
      setOffset(0)
      setListTick((t) => t + 1)
    } catch (e) {
      // 失败：toast + 表单保留已填内容不丢失
      notify('err', errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  const doVoid = async () => {
    if (!detail) return
    setVoiding(true)
    try {
      await api.voidTxn(detail.id)
      notify('ok', '已作废')
      setDetail(null)
      setVoidConfirm(false)
      reload()
      setOffset(0)
      setListTick((t) => t + 1)
    } catch (e) {
      notify('err', errText(e))
    } finally {
      setVoiding(false)
    }
  }

  const catName = (id?: string | null): string => {
    if (!id) return ''
    const c = categories.find((x) => x.id === id)
    if (!c) return ''
    if (c.parent_id) {
      const p = categories.find((x) => x.id === c.parent_id)
      return p ? `${p.name}·${c.name}` : c.name
    }
    return c.name
  }
  const accName = (id?: string | null): string =>
    id ? accounts.find((a) => a.id === id)?.name ?? '' : ''

  // chips 数据源（B1）
  const chips: { key: keyof Filters; label: string }[] = []
  if (filters.type) chips.push({ key: 'type', label: `类型：${filters.type === 'expense' ? '支出' : filters.type === 'income' ? '收入' : '转账'}` })
  if (filters.categoryId) chips.push({ key: 'categoryId', label: `分类：${catName(filters.categoryId)}` })
  if (filters.accountId) chips.push({ key: 'accountId', label: `账户：${accName(filters.accountId)}` })
  if (filters.start) chips.push({ key: 'start', label: `自 ${filters.start}` })
  if (filters.end) chips.push({ key: 'end', label: `至 ${filters.end}` })
  if (filters.keyword) chips.push({ key: 'keyword', label: `关键词：${filters.keyword}` })
  if (filters.minAmount) chips.push({ key: 'minAmount', label: `金额 ≥ ${filters.minAmount}` })
  if (filters.maxAmount) chips.push({ key: 'maxAmount', label: `金额 ≤ ${filters.maxAmount}` })
  if (filters.includeVoided) chips.push({ key: 'includeVoided', label: '含作废' })

  const setF = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }))

  return (
    <>
      <div className="grid gap-4">
        {/* U2 概览三卡（A1：三卡横排，语义色，tabular-nums；A3 状态齐备） */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {loading ? (
            [0, 1, 2].map((i) => (
              <div key={i} className="card bg-base-100 shadow">
                <div className="card-body py-5">
                  <div className="skeleton h-4 w-16" />
                  <div className="skeleton h-8 w-28" />
                </div>
              </div>
            ))
          ) : error ? (
            <div className="card bg-base-100 shadow md:col-span-3">
              <div className="card-body py-5 items-start gap-2">
                <span className="text-error text-sm">概览加载失败：{error}</span>
                <button className="btn btn-ghost btn-xs" onClick={reload}>重试</button>
              </div>
            </div>
          ) : (
            <>
              <div className="card bg-base-100 shadow">
                <div className="card-body py-5">
                  <div className="text-sm opacity-60">收入</div>
                  <div className={`text-2xl tabular-nums text-right ${amountColorClass('income')}`}>
                    +{thousands(overview?.income ?? '0.00')}
                  </div>
                </div>
              </div>
              <div className="card bg-base-100 shadow">
                <div className="card-body py-5">
                  <div className="text-sm opacity-60">支出</div>
                  <div className={`text-2xl tabular-nums text-right ${amountColorClass('expense')}`}>
                    −{thousands(overview?.expense ?? '0.00')}
                  </div>
                </div>
              </div>
              <div className="card bg-base-100 shadow">
                <div className="card-body py-5">
                  <div className="text-sm opacity-60">结余（{overview?.month ?? currentMonth()}）</div>
                  <div className="text-2xl tabular-nums text-right">
                    {thousands(overview?.balance ?? '0.00')}
                  </div>
                </div>
              </div>
            </>
          )}
        </section>

        {/* U1 筛选栏（B1：六件套 + 含作废开关 + 重置） */}
        <section className="card bg-base-100 shadow">
          <div className="card-body py-4 grid gap-3">
            <div className="flex flex-wrap gap-2 items-center">
              <select
                className="select select-bordered select-sm w-28"
                value={filters.type}
                onChange={(e) => setF({ type: e.target.value as Filters['type'] })}
              >
                <option value="">类型：全部</option>
                <option value="expense">支出</option>
                <option value="income">收入</option>
                <option value="transfer">转账</option>
              </select>
              <select
                className="select select-bordered select-sm w-36"
                value={filters.categoryId}
                onChange={(e) => setF({ categoryId: e.target.value })}
              >
                <option value="">分类：全部</option>
                {categories.filter((c) => !c.parent_id).map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
              <select
                className="select select-bordered select-sm w-36"
                value={filters.accountId}
                onChange={(e) => setF({ accountId: e.target.value })}
              >
                <option value="">账户：全部</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
              <input
                type="date"
                className="input input-bordered input-sm w-36"
                value={filters.start}
                onChange={(e) => setF({ start: e.target.value })}
                aria-label="起始日期"
              />
              <span className="opacity-50">~</span>
              <input
                type="date"
                className="input input-bordered input-sm w-36"
                value={filters.end}
                onChange={(e) => setF({ end: e.target.value })}
                aria-label="截止日期"
              />
              <input
                type="text"
                className="input input-bordered input-sm w-40"
                value={filters.keyword}
                onChange={(e) => setF({ keyword: e.target.value })}
                placeholder="关键词（备注/分类名）"
              />
              <input
                type="number"
                className="input input-bordered input-sm w-28"
                value={filters.minAmount}
                onChange={(e) => setF({ minAmount: e.target.value })}
                placeholder="最低金额"
                min="0"
                step="0.01"
              />
              <input
                type="number"
                className="input input-bordered input-sm w-28"
                value={filters.maxAmount}
                onChange={(e) => setF({ maxAmount: e.target.value })}
                placeholder="最高金额"
                min="0"
                step="0.01"
              />
              <label className="label cursor-pointer gap-2 py-0">
                <input
                  type="checkbox"
                  className="toggle toggle-sm"
                  checked={filters.includeVoided}
                  onChange={(e) => setF({ includeVoided: e.target.checked })}
                />
                <span className="label-text text-sm">含作废</span>
              </label>
              <button className="btn btn-ghost btn-sm" onClick={() => setFilters(EMPTY_FILTERS)}>
                重置
              </button>
            </div>
            {/* 条件摘要 chips（可单独移除） */}
            {hasFilters && (
              <div className="flex flex-wrap gap-1">
                {chips.map((c) => (
                  <span key={c.key} className="badge badge-ghost gap-1">
                    {c.label}
                    <button
                      aria-label="移除条件"
                      className="opacity-60 hover:opacity-100"
                      onClick={() => {
                        if (c.key === 'includeVoided') setF({ includeVoided: false })
                        else setF({ [c.key]: '' } as Partial<Filters>)
                      }}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* U1 列表主体（按日分组 + 滚动分页） */}
        <section className="card bg-base-100 shadow">
          <div className="card-body py-4 grid gap-0 p-0 overflow-hidden">
            {listLoading && items.length === 0 ? (
              <div className="p-6 grid gap-3">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="skeleton h-12 w-full" />
                ))}
              </div>
            ) : listError ? (
              <div className="p-6 text-error text-sm">加载失败：{listError}</div>
            ) : groups.length === 0 ? (
              <div className="p-10 text-center grid gap-3 justify-items-center">
                {hasFilters ? (
                  <>
                    <p className="opacity-60">没有符合条件的流水</p>
                    <button className="btn btn-ghost btn-sm" onClick={() => setFilters(EMPTY_FILTERS)}>
                      清空筛选
                    </button>
                  </>
                ) : (
                  <p className="opacity-60">本月还没有记账，点右上「记一笔」开始</p>
                )}
              </div>
            ) : (
              groups.map((g) => (
                <div key={g.day} data-testid="day-group">
                  {/* 分组头：日期 + 当日收支小计（不含转账/不计收支，B2） */}
                  <div className="flex items-center justify-between px-4 py-2 bg-base-200/60 text-sm sticky top-0 z-10">
                    <span className="font-medium">{groupTitle(g.day)}</span>
                    <span className="tabular-nums">
                      <span className={amountColorClass('income')}>
                        +{thousands(dayStats.get(g.day)?.income ?? '0.00')}
                      </span>
                      <span className="mx-1 opacity-30">/</span>
                      <span className={amountColorClass('expense')}>
                        −{thousands(dayStats.get(g.day)?.expense ?? '0.00')}
                      </span>
                    </span>
                  </div>
                  <ul>
                    {g.items.map((t) => {
                      const voided = t.status === 'voided'
                      const isTransfer = t.type === 'transfer'
                      const kind = kindOf(t.type, t.in_sum)
                      return (
                        <li key={t.id}>
                          <button
                            className={`w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-base-200/40 border-b border-base-200/60 min-h-11 ${voided ? 'opacity-60 line-through decoration-base-content/40' : ''}`}
                            onClick={() => {
                              setDetail(t)
                              setVoidConfirm(false)
                            }}
                          >
                            <span className="badge badge-ghost shrink-0">
                              {catName(t.category_id) || (isTransfer ? '转账' : '—')}
                            </span>
                            <span className="flex-1 min-w-0">
                              <span className="block truncate">
                                {isTransfer
                                  ? `${accName(t.account_id)} → ${accName(t.counter_account_id)}`
                                  : (t.note !== undefined && t.note !== null && t.note !== '') || catName(t.category_id)}
                              </span>
                              <span className="block text-xs opacity-50 truncate">
                                {accName(t.account_id)} · {hhmm(t.occurred_at)}
                              </span>
                            </span>
                            <span className="flex flex-col items-end gap-0.5 shrink-0">
                              <span className={`tabular-nums ${amountColorClass(kind)}`}>
                                {isTransfer ? '' : amountSign(kind)}
                                {thousands(t.amount)}
                              </span>
                              <span className="flex gap-1">
                                {t.location && (t.location.name || t.location.city) && (
                                  <span className="badge badge-ghost badge-xs">
                                    📍{t.location.name || t.location.city}
                                  </span>
                                )}
                                {isTransfer && <span className="badge badge-ghost badge-xs">转账</span>}
                                {!t.in_sum && !isTransfer && <span className="badge badge-ghost badge-xs">不计收支</span>}
                                {voided && <span className="badge badge-error badge-xs">已作废</span>}
                              </span>
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              ))
            )}
          </div>
          {/* 加载更多（时间倒序 offset 分页） */}
          {items.length < total && !listLoading && (
            <div className="p-3 text-center border-t border-base-200">
              <button className="btn btn-ghost btn-sm" onClick={() => setOffset(items.length)}>
                加载更多（已加载 {items.length}/{total}）
              </button>
            </div>
          )}
          {listLoading && items.length > 0 && (
            <div className="p-3 text-center text-xs opacity-50">加载中…</div>
          )}
        </section>
      </div>

      {/* 记一笔 / 编辑 Modal（C1：打开焦点落金额——autoFocus 于金额输入框） */}
      {formOpen && (
        <dialog className="modal modal-open" data-testid="txn-form-modal">
          <div className="modal-box max-w-md">
            <h3 className="font-semibold text-lg mb-3">
              {editing ? '编辑流水' : '记一笔'}
            </h3>
            <TransactionForm
              editing={editing}
              accounts={accounts}
              categories={categories}
              tags={tags}
              submitting={submitting}
              onCancel={closeForm}
              onSubmit={(payload) => void submitForm(payload)}
            />
          </div>
          <button className="modal-backdrop" onClick={closeForm} aria-label="关闭" />
        </dialog>
      )}

      {/* 详情抽屉（右侧滑出；D1-D3） */}
      {detail && (
        <dialog className="modal modal-end modal-open" data-testid="txn-detail-drawer">
          <div className="modal-box max-w-md">
            <TransactionDetail
              txn={detail}
              accounts={accounts}
              categories={categories}
              voiding={voiding}
              onClose={() => setDetail(null)}
              onEdit={() => openEdit(detail)}
              onVoid={() => setVoidConfirm(true)}
            />
          </div>
          <button className="modal-backdrop" onClick={() => setDetail(null)} aria-label="关闭" />
        </dialog>
      )}

      {/* 作废二次确认（D2：红色确认键 + 口径文案） */}
      {voidConfirm && detail && (
        <dialog className="modal modal-open" data-testid="void-confirm">
          <div className="modal-box max-w-sm">
            <h3 className="font-semibold text-lg">确认作废该笔流水？</h3>
            <p className="text-sm opacity-70 mt-2">
              作废后该笔不再参与统计，流水保留可追溯（可在「含作废」筛选中查看）。
            </p>
            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setVoidConfirm(false)}>
                取消
              </button>
              <button className="btn btn-error" disabled={voiding} onClick={() => void doVoid()}>
                {voiding && <span className="loading loading-spinner loading-xs" />}
                确认作废
              </button>
            </div>
          </div>
          <button className="modal-backdrop" onClick={() => setVoidConfirm(false)} aria-label="关闭" />
        </dialog>
      )}

      {/* 全局轻提示（成功/失败 toast；失败时表单已保留内容） */}
      {toast && (
        <div className="toast toast-end z-50">
          <div className={`alert ${toast.kind === 'ok' ? 'alert-success' : 'alert-error'} py-2 text-sm`}>
            <span>{toast.text}</span>
          </div>
        </div>
      )}
    </>
  )
}
