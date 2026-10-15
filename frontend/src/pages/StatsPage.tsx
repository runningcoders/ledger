import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Account, Category, GroupStat, LedgerPreset, StatsOut } from '../api/model'
import { api } from '../api/model'
import { currentMonth } from '../useLedgerData'
import { amountSign, thousands } from '../lib/format'

interface Props {
  data: { accounts: Account[]; categories: Category[] }
  /** 统计点击（扇区/图例/日历格）→ 流水页预置筛选（UI 规范 §5.1 动线） */
  onJump: (preset: LedgerPreset) => void
}

const PIE_COLORS = [
  '#f97316',
  '#0ea5e9',
  '#22c55e',
  '#a855f7',
  '#eab308',
  '#ef4444',
  '#14b8a6',
  '#f43f5e',
  '#8b5cf6',
  '#64748b',
]

type Month = string // 'YYYY-MM'

function shiftMonth(m: Month, delta: number): Month {
  const [y, mo] = m.split('-').map(Number)
  const d = new Date(y, mo - 1 + delta, 1)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}`
}

function monthBounds(m: Month): { start: string; end: string } {
  const [y, mo] = m.split('-').map(Number)
  const p = (n: number) => String(n).padStart(2, '0')
  return {
    start: `${y}-${p(mo)}-01 00:00:00`,
    end: `${new Date(y, mo, 0).getFullYear()}-${p(new Date(y, mo, 0).getMonth() + 1)}-${p(new Date(y, mo, 0).getDate())} 23:59:59`,
  }
}

function monthLabel(m: Month): string {
  const [y, mo] = m.split('-').map(Number)
  return `${y}年${mo}月`
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** 图表数值换算仅展示层（金额字符串→Number 供 recharts 渲染，聚合仍全走后端） */
function toNum(v: string): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

interface Filters {
  categoryId: string
  accountId: string
}

const EMPTY_FILTERS: Filters = { categoryId: '', accountId: '' }

/** 统计页（UI 规范 §5.1：分类占比 + 收支趋势 + 收支日历，月份切换，口径=R3/R4/R5 排除） */
export default function StatsPage({ data, onJump }: Props) {
  const { accounts, categories } = data
  const [month, setMonth] = useState<Month>(currentMonth())
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  // 趋势图粒度（§5.1：默认月粒度可切换日粒度）
  const [trendGrain, setTrendGrain] = useState<'month' | 'day'>('month')
  const [catOut, setCatOut] = useState<StatsOut | null>(null)
  const [trendOut, setTrendOut] = useState<StatsOut | null>(null)
  const [dayOut, setDayOut] = useState<StatsOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const bounds = useMemo(() => monthBounds(month), [month])
  // 趋势窗口：近 6 个月（含当前查看月）
  const trendBounds = useMemo(() => monthBounds(shiftMonth(month, -5)), [month])

  useEffect(() => {
    let alive = true
    const base = {
      start: bounds.start,
      end: bounds.end,
    }
    if (filters.categoryId) (base as Record<string, unknown>).category_id = filters.categoryId
    if (filters.accountId) (base as Record<string, unknown>).account_id = filters.accountId
    const trend = {
      start: trendBounds.start,
      end: bounds.end,
      ...(filters.categoryId ? { category_id: filters.categoryId } : {}),
      ...(filters.accountId ? { account_id: filters.accountId } : {}),
    }
    setLoading(true)
    setError(null)
    Promise.all([
      api.statsSummary({ ...base, group_by: 'category' }),
      api.statsSummary({ ...trend, group_by: trendGrain }),
      api.statsSummary({ ...base, group_by: 'day' }),
    ])
      .then(([cat, tr, day]) => {
        if (!alive) return
        setCatOut(cat)
        setTrendOut(tr)
        setDayOut(day)
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
  }, [bounds, trendBounds, filters, trendGrain])

  const catItems = catOut?.items ?? []
  const totalExpense = useMemo(
    () => catItems.reduce((sum, s) => sum + toNum(s.expense), 0),
    [catItems],
  )
  const pieData = useMemo(
    () =>
      catItems
        .filter((s) => toNum(s.expense) > 0)
        .map((s) => ({
          key: s.key ?? '',
          name: s.name ?? s.key ?? '未分类',
          value: toNum(s.expense),
          raw: s.expense,
        })),
    [catItems],
  )

  const trendData = useMemo(() => {
    const items = [...(trendOut?.items ?? [])].sort((a, b) =>
      (a.key ?? '').localeCompare(b.key ?? ''),
    )
    return items.map((s) => ({
      label: s.key
        ? trendGrain === 'month'
          ? `${Number(s.key.slice(5))}月`
          : s.key.slice(5) // day 粒度显示 MM-DD
        : '',
      income: toNum(s.income),
      expense: toNum(s.expense),
    }))
  }, [trendOut])

  // —— 收支日历（纯 DOM 月历格子，净额着色深浅 + 当日支出额）——
  const dayMap = useMemo(() => {
    const m = new Map<string, GroupStat>()
    for (const s of dayOut?.items ?? []) if (s.key) m.set(s.key, s)
    return m
  }, [dayOut])

  const calendar = useMemo(() => {
    const [y, mo] = month.split('-').map(Number)
    const firstDow = new Date(y, mo - 1, 1).getDay()
    const daysInMonth = new Date(y, mo, 0).getDate()
    const cells: ({ day: number; key: string } | null)[] = []
    for (let i = 0; i < firstDow; i++) cells.push(null)
    const p = (n: number) => String(n).padStart(2, '0')
    for (let d = 1; d <= daysInMonth; d++) {
      cells.push({ day: d, key: `${y}-${p(mo)}-${p(d)}` })
    }
    return cells
  }, [month])

  const maxAbsNet = useMemo(() => {
    let mx = 0
    for (const s of dayMap.values()) {
      const a = Math.abs(toNum(s.net))
      if (a > mx) mx = a
    }
    return mx
  }, [dayMap])

  const jumpCategory = (key: string) => {
    if (key) onJump({ categoryId: key })
  }

  return (
    <div className="grid gap-4">
      {/* 月份切换 + 分类/账户筛选（精确口径：透传 stats/summary category_id/account_id） */}
      <section className="card bg-base-100 shadow">
        <div className="card-body py-4 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1">
            <button
              className="btn btn-ghost btn-sm"
              aria-label="上一月"
              onClick={() => setMonth((m) => shiftMonth(m, -1))}
            >
              ←
            </button>
            <span className="font-medium tabular-nums px-1">{monthLabel(month)}</span>
            <button
              className="btn btn-ghost btn-sm"
              aria-label="下一月"
              onClick={() => setMonth((m) => shiftMonth(m, 1))}
            >
              →
            </button>
          </div>
          <div className="flex-1" />
          <select
            className="select select-bordered select-sm w-36"
            value={filters.categoryId}
            onChange={(e) => setFilters((f) => ({ ...f, categoryId: e.target.value }))}
          >
            <option value="">分类：全部</option>
            {categories
              .filter((c) => !c.parent_id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
          <select
            className="select select-bordered select-sm w-36"
            value={filters.accountId}
            onChange={(e) => setFilters((f) => ({ ...f, accountId: e.target.value }))}
          >
            <option value="">账户：全部</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          {(filters.categoryId || filters.accountId) && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              清除筛选
            </button>
          )}
        </div>
      </section>

      {loading ? (
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card bg-base-100 shadow">
              <div className="card-body py-6">
                <div className="skeleton h-4 w-24" />
                <div className="skeleton h-48 w-full" />
              </div>
            </div>
          ))}
        </section>
      ) : error ? (
        <div className="card bg-base-100 shadow">
          <div className="card-body py-6 items-start gap-2">
            <span className="text-error text-sm">统计加载失败：{error}</span>
          </div>
        </div>
      ) : (
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* ① 分类占比（支出；点击扇区/图例 → 流水页预置该分类筛选） */}
          <div className="card bg-base-100 shadow">
            <div className="card-body py-5">
              <h2 className="font-medium">分类占比（支出）</h2>
              {pieData.length === 0 ? (
                <div className="h-56 grid place-items-center opacity-60 text-sm">本月暂无数据</div>
              ) : (
                <div className="grid gap-3">
                  <div className="h-56">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={pieData}
                          dataKey="value"
                          nameKey="name"
                          innerRadius="45%"
                          outerRadius="80%"
                          onClick={(d: unknown) => {
                            const rec = d as { key?: string }
                            jumpCategory(rec.key ?? '')
                          }}
                        >
                          {pieData.map((entry, i) => (
                            <Cell key={entry.key} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(v) => toNum(String(v)).toFixed(2)} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <ul className="grid gap-1 text-sm">
                    {pieData.map((s, i) => (
                      <li key={s.key}>
                        <button
                          className="w-full flex items-center gap-2 text-left hover:bg-base-200/40 rounded px-1 py-0.5"
                          onClick={() => jumpCategory(s.key)}
                        >
                          <span
                            className="inline-block w-3 h-3 rounded-sm shrink-0"
                            style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }}
                          />
                          <span className="truncate">{s.name}</span>
                          <span className="flex-1" />
                          <span className="tabular-nums">{thousands(s.raw)}</span>
                          <span className="opacity-50 tabular-nums w-12 text-right">
                            {totalExpense > 0 ? Math.round((s.value / totalExpense) * 100) : 0}%
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          {/* ② 收支趋势（近 6 个月，月粒度双序列） */}
          <div className="card bg-base-100 shadow">
            <div className="card-body py-5">
              <div className="flex items-center justify-between">
                <h2 className="font-medium">收支趋势（近 6 个月）</h2>
                <div className="flex gap-1">
                  <button
                    className={`btn btn-xs ${trendGrain === 'month' ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setTrendGrain('month')}
                  >
                    月
                  </button>
                  <button
                    className={`btn btn-xs ${trendGrain === 'day' ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setTrendGrain('day')}
                  >
                    日
                  </button>
                </div>
              </div>
              {trendData.length === 0 ? (
                <div className="h-56 grid place-items-center opacity-60 text-sm">暂无数据</div>
              ) : (
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={trendData}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="label" tickLine={false} />
                      <YAxis tickLine={false} width={56} />
                      <Tooltip formatter={(v) => toNum(String(v)).toFixed(2)} />
                      <Bar dataKey="income" name="收入" fill="#22c55e" radius={[3, 3, 0, 0]} />
                      <Bar dataKey="expense" name="支出" fill="#ef4444" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>

          {/* ③ 收支日历（净额着色深浅 + 当日支出；点击某日 → 流水页预置该日筛选） */}
          <div className="card bg-base-100 shadow lg:col-span-2">
            <div className="card-body py-5">
              <h2 className="font-medium">收支日历</h2>
              <div className="grid grid-cols-7 gap-1 text-xs opacity-60 text-center">
                {['日', '一', '二', '三', '四', '五', '六'].map((w) => (
                  <span key={w}>{w}</span>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1">
                {calendar.map((cell, i) =>
                  cell === null ? (
                    <span key={`pad-${i}`} />
                  ) : (
                    (() => {
                      const stat = dayMap.get(cell.key)
                      const net = stat ? toNum(stat.net) : 0
                      const ratio = maxAbsNet > 0 ? Math.abs(net) / maxAbsNet : 0
                      return (
                        <button
                          key={cell.key}
                          className="relative rounded border border-base-200 min-h-16 p-1 text-left overflow-hidden hover:border-primary/60"
                          onClick={() => onJump({ start: cell.key, end: cell.key })}
                        >
                          {stat && ratio > 0 && (
                            <span
                              className={`absolute inset-0 ${net >= 0 ? 'bg-success' : 'bg-error'}`}
                              style={{ opacity: 0.08 + ratio * 0.3 }}
                            />
                          )}
                          <span className="relative block text-xs opacity-70 tabular-nums">
                            {cell.day}
                          </span>
                          {stat && toNum(stat.expense) > 0 && (
                            <span className="relative block text-xs tabular-nums text-error">
                              {amountSign('expense')}
                              {thousands(stat.expense)}
                            </span>
                          )}
                        </button>
                      )
                    })()
                  ),
                )}
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
