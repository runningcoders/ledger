import { useCallback, useEffect, useState } from 'react'
import { api } from './api/model'
import type { Account, Category, Overview, Tag } from './api/model'

export interface LedgerData {
  overview: Overview | null
  accounts: Account[]
  categories: Category[]
  tags: Tag[]
}

/** 全局基础数据：概览 + 账户 + 分类 + 标签（reload 供增删改后刷新） */
export function useLedgerData(): {
  data: LedgerData
  loading: boolean
  error: string | null
  reload: () => void
} {
  const [data, setData] = useState<LedgerData>({
    overview: null,
    accounts: [],
    categories: [],
    tags: [],
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  const reload = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    Promise.all([
      api.overview(),
      api.listAccounts(),
      api.listCategories(),
      api.listTags(),
    ])
      .then(([overview, accounts, categories, tags]) => {
        if (!alive) return
        setData({ overview, accounts, categories, tags })
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [tick])

  return { data, loading, error, reload }
}

/** 「本月」口径：YYYY-MM（本地时区，自然月） */
export function currentMonth(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}`
}
