import { useCallback, useEffect, useState } from 'react'
import type { LedgerPreset, TxnCreate } from './api/model'
import { api, clearCredential, getCredential } from './api/model'
import { useLedgerData } from './useLedgerData'
import LedgerPage from './pages/LedgerPage'
import StatsPage from './pages/StatsPage'
import BudgetPage from './pages/BudgetPage'
import AccountsPage from './pages/AccountsPage'
import TransactionForm from './components/TransactionForm'
import CredentialModal from './components/CredentialModal'

type Tab = 'ledger' | 'stats' | 'budget' | 'accounts'

const TABS: { key: Tab; label: string }[] = [
  { key: 'ledger', label: '流水' },
  { key: 'stats', label: '统计' },
  { key: 'budget', label: '预算' },
  { key: 'accounts', label: '账户' },
]

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** 应用根：一级导航四页（UI 规范 §2.2）+ 全局记一笔恒可达 + 跨页预置筛选动线 */
export default function App() {
  const [tab, setTab] = useState<Tab>('ledger')
  // ── 总管模式（评估面 d，AMan 拍板②按用户切换）：health.mode 驱动；单用户模式全零渲染 ──
  const [mode, setMode] = useState<'single' | 'manager' | null>(null)
  const [users, setUsers] = useState<string[]>([])
  const [activeUid, setActiveUidState] = useState<string | null>(null)
  const [credOpen, setCredOpen] = useState(false)
  const [hasCred, setHasCred] = useState(getCredential() !== null)
  const { data, loading, error, reload } = useLedgerData(
    mode === 'manager' ? activeUid : null,
  )

  useEffect(() => {
    let alive = true
    api
      .health()
      .then((h) => {
        if (!alive) return
        setMode(h.mode === 'manager' ? 'manager' : 'single')
        if (h.mode === 'manager') void api.getUsers().then((u) => {
          if (alive) setUsers(u.items)
        })
      })
      .catch(() => {
        if (alive) setMode('single') // 探测失败按单用户兜底（本地服务形态不变）
      })
    return () => {
      alive = false
    }
  }, [])

  // 后端 401 → request() 已清凭证态并派发事件 → 此处自动重弹「解锁写入」
  useEffect(() => {
    const onRequired = () => {
      setHasCred(false)
      setCredOpen(true)
    }
    window.addEventListener('ledger:credential-required', onRequired)
    return () => window.removeEventListener('ledger:credential-required', onRequired)
  }, [])

  const isManager = mode === 'manager'
  // 切换用户即清写凭证（凭证写目标=其解析 uid，跨用户残留态易误写他库）
  const switchUser = useCallback((uid: string | null) => {
    setActiveUidState(uid)
    clearCredential()
    setHasCred(false)
  }, [])
  // 全局记一笔（所有页面恒可见恒可达）
  const [formOpen, setFormOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // 统计页点击扇区/日历格、账户页点击账户卡 → 流水页预置筛选（一次性消费）
  const [preset, setPreset] = useState<LedgerPreset | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const notify = useCallback((kind: 'ok' | 'err', text: string) => {
    setToast({ kind, text })
    window.setTimeout(() => setToast(null), 2500)
  }, [])

  const jumpToLedger = useCallback((p: LedgerPreset) => {
    setPreset(p)
    setTab('ledger')
  }, [])

  const consumePreset = useCallback(() => setPreset(null), [])

  const closeForm = () => {
    if (!submitting) setFormOpen(false)
  }

  const submitForm = async (payload: unknown) => {
    setSubmitting(true)
    try {
      await api.createTxn(payload as TxnCreate)
      setFormOpen(false)
      notify('ok', '已记一笔')
      reload()
    } catch (e) {
      notify('err', errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-base-200">
      <div className="mx-auto max-w-5xl px-4 py-6 grid gap-4">
        {/* 顶栏：标题 + 一级导航（当前项高亮）+ 记一笔；总管模式追加用户切换器 */}
        <header className="flex items-center justify-between gap-3 flex-wrap">
          <h1 className="font-display text-xl font-semibold">个人流水账</h1>
          <nav className="flex gap-1" aria-label="一级导航">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`btn btn-sm ${tab === t.key ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            {isManager && (
              <select
                data-testid="user-switcher"
                aria-label="用户切换（总管模式）"
                className="select select-sm select-bordered"
                value={activeUid ?? ''}
                onChange={(e) => switchUser(e.target.value || null)}
              >
                <option value="">全部用户（聚合）</option>
                {users.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
            )}
            {isManager && (
              <button
                className={`btn btn-sm ${hasCred ? 'btn-success' : 'btn-outline'}`}
                data-testid="unlock-write"
                onClick={() => setCredOpen(true)}
              >
                {hasCred ? '已解锁写入' : '解锁写入'}
              </button>
            )}
            <span className="text-xs opacity-60">本地服务 127.0.0.1:8723</span>
            <button className="btn btn-primary" onClick={() => setFormOpen(true)}>
              记一笔
            </button>
          </div>
        </header>

        {tab === 'ledger' && (
          <LedgerPage
            key={activeUid ?? 'all'}
            data={data}
            loading={loading}
            error={error}
            reload={reload}
            preset={preset}
            onPresetConsumed={consumePreset}
          />
        )}
        {tab === 'stats' && (
          <StatsPage
            key={activeUid ?? 'all'}
            data={{ accounts: data.accounts, categories: data.categories }}
            onJump={jumpToLedger}
          />
        )}
        {tab === 'budget' && <BudgetPage key={activeUid ?? 'all'} data={data} reload={reload} />}
        {tab === 'accounts' && (
          <AccountsPage
            key={activeUid ?? 'all'}
            data={data}
            reload={reload}
            onJumpAccount={jumpToLedger}
          />
        )}
      </div>

      {/* 全局记一笔 Modal（新建；编辑走流水页详情抽屉） */}
      {formOpen && (
        <dialog className="modal modal-open" data-testid="txn-form-modal">
          <div className="modal-box max-w-md">
            <h3 className="font-semibold text-lg mb-3">记一笔</h3>
            <TransactionForm
              editing={null}
              accounts={data.accounts}
              categories={data.categories}
              tags={data.tags}
              submitting={submitting}
              onCancel={closeForm}
              onSubmit={(payload) => void submitForm(payload)}
            />
          </div>
          <button className="modal-backdrop" onClick={closeForm} aria-label="关闭" />
        </dialog>
      )}

      {/* 总管模式：解锁写入凭证弹窗（401 自动重弹） */}
      {isManager && (
        <CredentialModal
          open={credOpen}
          onClose={() => setCredOpen(false)}
          onUnlocked={() => setHasCred(getCredential() !== null)}
        />
      )}

      {/* 全局轻提示（成功/失败 toast） */}
      {toast && (
        <div className="toast toast-end z-50">
          <div className={`alert ${toast.kind === 'ok' ? 'alert-success' : 'alert-error'} py-2 text-sm`}>
            <span>{toast.text}</span>
          </div>
        </div>
      )}
    </div>
  )
}
