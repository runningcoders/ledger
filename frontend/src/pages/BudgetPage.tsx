import { useCallback, useEffect, useMemo, useState } from 'react'
import type { BudgetLine, BudgetSetItem } from '../api/model'
import type { LedgerData } from '../useLedgerData'
import { api } from '../api/model'
import { currentMonth } from '../useLedgerData'
import { thousands } from '../lib/format'

interface Props {
  data: LedgerData
  reload: () => void
}

type Month = string // 'YYYY-MM'

function shiftMonth(m: Month, delta: number): Month {
  const [y, mo] = m.split('-').map(Number)
  const d = new Date(y, mo - 1 + delta, 1)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}`
}

function monthLabel(m: Month): string {
  const [y, mo] = m.split('-').map(Number)
  return `${y}年${mo}月`
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** R7 三态 → 进度条语义色（正常主色 / ≥80% 临近警示 / ≥100% 超支错误） */
function barClass(status: BudgetLine['status']): string {
  if (status === 'over') return 'bg-error'
  if (status === 'near_limit') return 'bg-warning'
  return 'bg-primary'
}

function badgeClass(status: BudgetLine['status']): string {
  if (status === 'over') return 'badge-error'
  if (status === 'near_limit') return 'badge-warning'
  return 'badge-ghost'
}

function statusText(status: BudgetLine['status']): string {
  if (status === 'over') return '超支'
  if (status === 'near_limit') return '临近超支'
  return '正常'
}

/** 进度条宽度（展示层换算：used/amount 百分比，聚合仍全走后端） */
function barWidth(used: string, amount: string): number {
  const a = Number(amount)
  if (!Number.isFinite(a) || a <= 0) return 0
  const u = Number(used)
  if (!Number.isFinite(u) || u <= 0) return 0
  return Math.min(100, Math.round((u / a) * 100))
}

function lineTitle(line: BudgetLine): string {
  return line.category_id ? (line.category_name ?? '分类预算') : '总预算'
}

/** 预算页（UI 规范 §5.2：总预算卡 + 分类预算列表 + 三态进度条 + 设定弹窗；R7 不推送） */
export default function BudgetPage({ data, reload }: Props) {
  const { categories } = data
  const [month, setMonth] = useState<Month>(currentMonth())
  const [lines, setLines] = useState<BudgetLine[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  // 设定弹窗：总预算金额 + 各分类预算金额（固定按月，只设金额）
  const [totalAmount, setTotalAmount] = useState('')
  const [catAmounts, setCatAmounts] = useState<Record<string, string>>({})
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const notify = useCallback((kind: 'ok' | 'err', text: string) => {
    setToast({ kind, text })
    window.setTimeout(() => setToast(null), 2500)
  }, [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    api
      .budgetStatus(month)
      .then((out) => {
        if (!alive) return
        setLines(out.lines)
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(errText(e))
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [month])

  const totalLine = lines.find((l) => !l.category_id) ?? null
  const categoryLines = useMemo(() => lines.filter((l) => l.category_id), [lines])
  // 一级支出分类（预算挂在父级：used=本级+直接子级，R6 两级封顶）
  const expenseRoots = useMemo(
    () => categories.filter((c) => !c.parent_id && c.direction === 'expense'),
    [categories],
  )

  const openDialog = () => {
    setTotalAmount(totalLine?.amount ?? '')
    const init: Record<string, string> = {}
    for (const l of categoryLines) if (l.category_id) init[l.category_id] = l.amount
    setCatAmounts(init)
    setFormError(null)
    setDialogOpen(true)
  }

  const submitBudgets = async () => {
    const items: BudgetSetItem[] = []
    if (totalAmount.trim() !== '') items.push({ category_id: null, amount: totalAmount.trim() })
    for (const [cid, v] of Object.entries(catAmounts)) {
      if (v.trim() !== '') items.push({ category_id: cid, amount: v.trim() })
    }
    if (items.length === 0) {
      setFormError('至少设定一项预算（留空=不设定该期预算）')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      const out = await api.setBudgets({ period: month, items })
      setLines(out.lines)
      setDialogOpen(false)
      notify('ok', '预算已保存')
      reload()
    } catch (e) {
      setFormError(errText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid gap-4">
      <section className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1">
          <button
            className="btn btn-ghost btn-sm"
            aria-label="上一月"
            onClick={() => setMonth((m) => shiftMonth(m, -1))}
          >
            ←
          </button>
          <span className="font-medium tabular-nums px-1">预算 · {monthLabel(month)}</span>
          <button
            className="btn btn-ghost btn-sm"
            aria-label="下一月"
            onClick={() => setMonth((m) => shiftMonth(m, 1))}
          >
            →
          </button>
        </div>
        <div className="flex-1" />
        <button className="btn btn-primary btn-sm" onClick={openDialog}>
          设定预算
        </button>
      </section>

      {loading ? (
        <section className="grid gap-4">
          {[0, 1].map((i) => (
            <div key={i} className="card bg-base-100 shadow">
              <div className="card-body py-5">
                <div className="skeleton h-4 w-20" />
                <div className="skeleton h-3 w-full" />
              </div>
            </div>
          ))}
        </section>
      ) : error ? (
        <div className="card bg-base-100 shadow">
          <div className="card-body py-5 items-start gap-2">
            <span className="text-error text-sm">预算加载失败：{error}</span>
          </div>
        </div>
      ) : (
        <section className="grid gap-4">
          {/* 总预算卡 */}
          {totalLine ? (
            <div className="card bg-base-100 shadow">
              <div className="card-body py-5 grid gap-2">
                <div className="flex items-center justify-between">
                  <h2 className="font-medium">总预算</h2>
                  <span className={`badge ${badgeClass(totalLine.status)}`}>
                    {statusText(totalLine.status)}
                  </span>
                </div>
                <div className="flex items-baseline justify-between tabular-nums">
                  <span className="text-sm opacity-60">
                    已用 {thousands(totalLine.used)} / 预算 {thousands(totalLine.amount)}
                  </span>
                  <span className="text-lg">
                    {totalLine.status === 'over' && totalLine.remaining.startsWith('-') ? (
                      <span className="text-error">
                        超出 {thousands(totalLine.remaining.slice(1))}
                      </span>
                    ) : (
                      <>
                        <span className="text-sm opacity-60">剩余 </span>
                        {thousands(totalLine.remaining)}
                      </>
                    )}
                  </span>
                </div>
                <progress
                  className={`progress ${totalLine.status === 'over' ? 'progress-error' : totalLine.status === 'near_limit' ? 'progress-warning' : 'progress-primary'}`}
                  value={barWidth(totalLine.used, totalLine.amount)}
                  max={100}
                />
              </div>
            </div>
          ) : (
            <div className="card bg-base-100 shadow">
              <div className="card-body py-5 opacity-60 text-sm">
                本期未设总预算，点右上「设定预算」开始
              </div>
            </div>
          )}

          {/* 分类预算列表（无预算分类不出现——后端只返回已设行） */}
          {categoryLines.length === 0 ? (
            <div className="card bg-base-100 shadow">
              <div className="card-body py-5 opacity-60 text-sm">本期未设分类预算</div>
            </div>
          ) : (
            categoryLines.map((l) => (
              <div key={l.category_id ?? l.category_name} className="card bg-base-100 shadow">
                <div className="card-body py-4 grid gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {l.category_id ? (
                        <span className="badge orz-tag badge-sm">{lineTitle(l)}</span>
                      ) : (
                        <span className="font-medium">{lineTitle(l)}</span>
                      )}
                      <span className={`badge badge-sm ${badgeClass(l.status)}`}>
                        {statusText(l.status)}
                      </span>
                    </div>
                    <span className="text-sm tabular-nums">
                      {thousands(l.used)} / {thousands(l.amount)}
                      {l.status === 'over' && l.remaining.startsWith('-') && (
                        <span className="text-error ml-2">
                          超出 {thousands(l.remaining.slice(1))}
                        </span>
                      )}
                    </span>
                  </div>
                  <progress
                    className={`progress ${barClass(l.status) === 'bg-error' ? 'progress-error' : barClass(l.status) === 'bg-warning' ? 'progress-warning' : 'progress-primary'}`}
                    value={barWidth(l.used, l.amount)}
                    max={100}
                  />
                </div>
              </div>
            ))
          )}
        </section>
      )}

      {/* 设定预算弹窗（总预算 + 各一级支出分类；只设金额，固定按月） */}
      {dialogOpen && (
        <dialog className="modal modal-open" data-testid="budget-set-modal">
          <div className="modal-box max-w-md">
            <h3 className="font-semibold text-lg mb-1">设定预算（{monthLabel(month)}）</h3>
            <p className="text-xs opacity-60 mb-3">留空表示不设定该项；保存将整体替换本期预算。</p>
            <div className="grid gap-3">
              <label className="form-control">
                <div className="label py-1">
                  <span className="label-text">总预算</span>
                </div>
                <input
                  type="number"
                  className="input input-bordered input-sm"
                  value={totalAmount}
                  onChange={(e) => setTotalAmount(e.target.value)}
                  placeholder="留空=不设总预算"
                  min="0"
                  step="0.01"
                />
              </label>
              <div className="divider my-0 text-xs opacity-50">分类预算</div>
              {expenseRoots.map((c) => (
                <label key={c.id} className="form-control">
                  <div className="label py-1">
                    <span className="label-text">{c.name}</span>
                  </div>
                  <input
                    type="number"
                    className="input input-bordered input-sm"
                    value={catAmounts[c.id] ?? ''}
                    onChange={(e) =>
                      setCatAmounts((m) => ({ ...m, [c.id]: e.target.value }))
                    }
                    placeholder="留空=不设"
                    min="0"
                    step="0.01"
                  />
                </label>
              ))}
              {formError && <p className="text-error text-sm">{formError}</p>}
            </div>
            <div className="modal-action">
              <button
                className="btn btn-ghost"
                disabled={saving}
                onClick={() => setDialogOpen(false)}
              >
                取消
              </button>
              <button
                className="btn btn-primary"
                disabled={saving}
                onClick={() => void submitBudgets()}
              >
                {saving && <span className="loading loading-spinner loading-xs" />}
                保存
              </button>
            </div>
          </div>
          <button
            className="modal-backdrop"
            onClick={() => setDialogOpen(false)}
            aria-label="关闭"
          />
        </dialog>
      )}

      {toast && (
        <div className="toast toast-end z-50">
          <div
            className={`alert ${toast.kind === 'ok' ? 'alert-success' : 'alert-error'} py-2 text-sm`}
          >
            <span>{toast.text}</span>
          </div>
        </div>
      )}
    </div>
  )
}
