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
export type AccountCreate = Schemas['AccountCreate']
export type AccountUpdate = Schemas['AccountUpdate']
export type Category = Schemas['CategoryOut']
export type CategoryCreate = Schemas['CategoryCreate']
export type CategoryUpdate = Schemas['CategoryUpdate']
export type Tag = Schemas['TagOut']
export type TagRenameRequest = Schemas['TagRenameRequest']
export type LocationInfo = Schemas['LocationInfo']
export type StatsSummary = Schemas['GroupStat']
export type GroupStat = Schemas['GroupStat']
export type StatsOut = Schemas['StatsOut']
export type StatsQuery = NonNullable<
  operations['stats_summary_endpoint_api_v1_stats_summary_get']['parameters']['query']
>
export type BudgetLine = Schemas['BudgetLine']
export type BudgetStatusOut = Schemas['BudgetStatusOut']
export type BudgetSetRequest = Schemas['BudgetSetRequest']
export type BudgetSetItem = Schemas['BudgetSetItem']

export type ListQuery = NonNullable<
  operations['list_transactions_endpoint_api_v1_transactions_get']['parameters']['query']
>

/** 统计页/账户页 → 流水页预置筛选动线（UI 规范 §5.1 点击扇区/日历、§5.3 点击账户卡） */
export interface LedgerPreset {
  categoryId?: string
  accountId?: string
  /** 'YYYY-MM-DD'（日历格单日跳转） */
  start?: string
  end?: string
}

/** 后端 Base URL：生产态由后端同源托管（空串=同源），开发态走 vite proxy */
const BASE = ''

// ── 总管模式：写凭证与用户切换（评估报告 01a14009 评估面 c/d，AMan 拍板②③④按推荐）──
// 凭证态：内存 + sessionStorage（会话级，浏览器关闭即清；不落 localStorage 明文）。
// 写请求由 request() 单点自动注入 X-Ledger-Credential 头（全部写调用点零逐改）；
// 读请求由 request() 单点自动拼接 uid 查询参数（activeUid 非空时；单用户模式恒空=零影响）。
const CRED_KEY = 'ledger_write_credential'
let credential: string | null = null
try {
  credential = sessionStorage.getItem(CRED_KEY)
} catch {
  credential = null // sessionStorage 不可用（隐私模式等）则仅内存态
}
let activeUid: string | null = null

export function getCredential(): string | null {
  return credential
}

export function setCredential(value: string): void {
  credential = value
  try {
    sessionStorage.setItem(CRED_KEY, value)
  } catch {
    /* 会话存储不可用则仅内存态 */
  }
}

export function clearCredential(): void {
  credential = null
  try {
    sessionStorage.removeItem(CRED_KEY)
  } catch {
    /* ignore */
  }
}

export function setActiveUid(uid: string | null): void {
  activeUid = uid
}

function withUid(path: string): string {
  if (!activeUid) return path
  return `${path}${path.includes('?') ? '&' : '?'}uid=${encodeURIComponent(activeUid)}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  const headers = new Headers(init?.headers)
  const isWrite = method !== 'GET' && method !== 'HEAD'
  if (isWrite && credential) headers.set('X-Ledger-Credential', credential)
  const res = await fetch(`${BASE}${isWrite ? path : withUid(path)}`, { ...init, headers })
  if (res.status === 401) {
    // 评估面 c：失败不静默——清凭证态 + 事件通知顶栏重弹「解锁写入」
    clearCredential()
    window.dispatchEvent(new CustomEvent('ledger:credential-required'))
    const body = (await res.json().catch(() => null)) as
      | { error?: { message?: string } }
      | null
    throw new Error(body?.error?.message ?? '需要写凭证（HTTP 401）')
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { error?: { message?: string } }
      | null
    throw new Error(body?.error?.message ?? `请求失败（HTTP ${res.status}）`)
  }
  return (await res.json()) as T
}

function qs(params: object): string {
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

function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

export const api = {
  health: () =>
    request<{ status: string; mode: 'single' | 'manager'; user: string | null }>(
      '/api/health',
    ),

  /** 已注册用户清单（总管模式切换器数据源；单用户模式恒空=切换器不渲染） */
  getUsers: () => request<{ items: string[] }>('/api/v1/users'),

  listTxns: (params: Partial<ListQuery>) =>
    request<TxnList>(`/api/v1/transactions${qs(params)}`),

  getTxn: (id: string) => request<Txn>(`/api/v1/transactions/${id}`),

  createTxn: (payload: TxnCreate) =>
    request<Txn>('/api/v1/transactions', jsonInit('POST', payload)),

  updateTxn: (id: string, payload: TxnUpdate) =>
    request<Txn>(`/api/v1/transactions/${id}`, jsonInit('PATCH', payload)),

  voidTxn: (id: string) =>
    request<Txn>(`/api/v1/transactions/${id}/void`, { method: 'POST' }),

  overview: (month?: string) =>
    request<Overview>(`/api/v1/overview${month ? `?month=${month}` : ''}`),

  listAccounts: () => request<Account[]>('/api/v1/accounts'),

  createAccount: (payload: AccountCreate) =>
    request<Account>('/api/v1/accounts', jsonInit('POST', payload)),

  updateAccount: (id: string, payload: AccountUpdate) =>
    request<Account>(`/api/v1/accounts/${id}`, jsonInit('PATCH', payload)),

  listCategories: (enabledOnly = false) =>
    request<Category[]>(`/api/v1/categories${enabledOnly ? '?enabled_only=true' : ''}`),

  createCategory: (payload: CategoryCreate) =>
    request<Category>('/api/v1/categories', jsonInit('POST', payload)),

  updateCategory: (id: string, payload: CategoryUpdate) =>
    request<Category>(`/api/v1/categories/${id}`, jsonInit('PATCH', payload)),

  listTags: () => request<Tag[]>('/api/v1/tags'),

  renameTag: (id: string, payload: TagRenameRequest) =>
    request<Tag>(`/api/v1/tags/${id}`, jsonInit('PATCH', payload)),

  /** 预算状态（R7 三态；period 缺省本月；总预算行 category_id=null 在前） */
  budgetStatus: (period?: string) =>
    request<BudgetStatusOut>(`/api/v1/budgets${period ? `?period=${period}` : ''}`),

  /** 设定预算（PUT 整体替换该期全部行） */
  setBudgets: (payload: BudgetSetRequest) =>
    request<BudgetStatusOut>('/api/v1/budgets', jsonInit('PUT', payload)),

  /** 通用统计聚合（P1：stats/summary 含分类/账户/标签筛选维度，聚合全走后端） */
  statsSummary: (params: Partial<StatsQuery>) =>
    request<StatsOut>(`/api/v1/stats/summary${qs(params)}`),

  /** 按日粒度统计（B2 当日收支小计口径源：排除转账/不计收支/作废，与概览同口径）；
   *  P1 精确口径：可透传分类/账户筛选（后端扩展参数，分组∈GROUP BY 与筛选∈WHERE 正交） */
  dailyStats: (
    start: string,
    end: string,
    filters?: { category_id?: string; account_id?: string },
  ) =>
    request<StatsOut>(
      `/api/v1/stats/summary${qs({ start, end, group_by: 'day', ...filters })}`,
    ),
}
