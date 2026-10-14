/**
 * API 模型与请求封装（前端消费层）。
 * 类型全部派生自生成式契约 src/api/types.ts（后端 models.py 单一事实源，
 * 由 `npm run gen:api` 产出、禁手改）；本文件只做别名与封装，零类型手写。
 */
import type { components, operations } from './types'

type Schemas = components['schemas']

export type Txn = Schemas['TransactionOut']
export type TxnList = Schemas['TransactionListOut']
export type TxnCreate = Schemas['TransactionCreate']
export type TxnUpdate = Schemas['TransactionUpdate']
export type Overview = Schemas['OverviewOut']
export type Account = Schemas['AccountOut']
export type Category = Schemas['CategoryOut']
export type Tag = Schemas['TagOut']
export type StatsSummary = Schemas['GroupStat']
export type StatsOut = Schemas['StatsOut']

export type ListQuery = NonNullable<
  operations['list_transactions_endpoint_api_v1_transactions_get']['parameters']['query']
>

/** 后端 Base URL：生产态由后端同源托管（空串=同源），开发态走 vite proxy */
const BASE = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { error?: { message?: string } }
      | null
    throw new Error(body?.error?.message ?? `请求失败（HTTP ${res.status}）`)
  }
  return (await res.json()) as T
}

function qs(params: Partial<ListQuery>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) {
      for (const item of v) sp.append(k, String(item))
    } else {
      sp.append(k, String(v))
    }
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const api = {
  health: () => request<{ status: string }>('/api/health'),

  listTxns: (params: Partial<ListQuery>) =>
    request<TxnList>(`/api/v1/transactions${qs(params)}`),

  getTxn: (id: string) => request<Txn>(`/api/v1/transactions/${id}`),

  createTxn: (payload: TxnCreate) =>
    request<Txn>('/api/v1/transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  updateTxn: (id: string, payload: TxnUpdate) =>
    request<Txn>(`/api/v1/transactions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  voidTxn: (id: string) =>
    request<Txn>(`/api/v1/transactions/${id}/void`, { method: 'POST' }),

  overview: (month?: string) =>
    request<Overview>(`/api/v1/overview${month ? `?month=${month}` : ''}`),

  listAccounts: () => request<Account[]>('/api/v1/accounts'),

  listCategories: (enabledOnly = false) =>
    request<Category[]>(`/api/v1/categories${enabledOnly ? '?enabled_only=true' : ''}`),

  listTags: () => request<Tag[]>('/api/v1/tags'),

  /** 按日粒度统计（B2 当日收支小计口径源：排除转账/不计收支/作废，与概览同口径） */
  dailyStats: (start: string, end: string) =>
    request<StatsOut>(
      `/api/v1/stats/summary?start=${start}&end=${end}&group_by=day`,
    ),
}
