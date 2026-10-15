import { useMemo, useState } from 'react'
import type {
  Account,
  AccountCreate,
  Category,
  LedgerPreset,
} from '../api/model'
import type { LedgerData } from '../useLedgerData'
import { api } from '../api/model'
import { thousands } from '../lib/format'

interface Props {
  data: LedgerData
  reload: () => void
  /** 点击账户卡 → 流水页预置该账户筛选（UI 规范 §5.3 动线） */
  onJumpAccount: (preset: LedgerPreset) => void
}

type Tab = 'accounts' | 'categories' | 'tags'

const TYPE_LABEL: Record<Account['type'], string> = {
  cash: '现金',
  debit: '借记卡',
  credit: '信用卡',
  virtual: '虚拟账户',
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/**
 * 金额字符串精确加总（仅用于「资产合计」展示，待议项：与「前端零金额算术」纪律的
 * 边界已列 TL 裁定）。实现取整数分 BigInt 求和，零 float，两位小数口径不破坏。
 */
function sumAmounts(amounts: string[]): string {
  let cents = 0n
  for (const raw of amounts) {
    const s = raw.trim() || '0'
    const neg = s.startsWith('-')
    const body = neg ? s.slice(1) : s
    const [int, frac = ''] = body.split('.')
    const frac2 = (frac + '00').slice(0, 2)
    const v = BigInt(int || '0') * 100n + BigInt(frac2 || '0')
    cents += neg ? -v : v
  }
  const neg = cents < 0n
  const abs = neg ? -cents : cents
  const int = abs / 100n
  const frac = (abs % 100n).toString().padStart(2, '0')
  return `${neg ? '-' : ''}${int}.${frac}`
}

/** 账户页（UI 规范 §5.3：账户卡列表+合计行+新增/编辑弹窗[不做删除]+分类/标签管理次级 Tab） */
export default function AccountsPage({ data, reload, onJumpAccount }: Props) {
  const { accounts, categories, tags } = data
  const [tab, setTab] = useState<Tab>('accounts')
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const notify = (kind: 'ok' | 'err', text: string) => {
    setToast({ kind, text })
    window.setTimeout(() => setToast(null), 2500)
  }

  const run = async (action: () => Promise<unknown>, okText: string) => {
    setBusy(true)
    try {
      await action()
      notify('ok', okText)
      reload()
      return true
    } catch (e) {
      notify('err', errText(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  // —— 账户弹窗态 ——
  const [accDialog, setAccDialog] = useState<
    { mode: 'create' } | { mode: 'edit'; account: Account } | null
  >(null)
  const [accName, setAccName] = useState('')
  const [accType, setAccType] = useState<Account['type']>('cash')
  const [accCurrency, setAccCurrency] = useState('CNY')
  const [accInitial, setAccInitial] = useState('0')
  const [accEnabled, setAccEnabled] = useState(true)
  const [accError, setAccError] = useState<string | null>(null)

  const openAccCreate = () => {
    setAccName('')
    setAccType('cash')
    setAccCurrency('CNY')
    setAccInitial('0')
    setAccError(null)
    setAccDialog({ mode: 'create' })
  }
  const openAccEdit = (a: Account) => {
    setAccName(a.name)
    setAccType(a.type)
    setAccEnabled(a.enabled)
    setAccError(null)
    setAccDialog({ mode: 'edit', account: a })
  }

  const submitAccount = async () => {
    if (!accDialog) return
    if (accName.trim() === '') {
      setAccError('请填写账户名称')
      return
    }
    if (accDialog.mode === 'create') {
      const payload: AccountCreate = {
        name: accName.trim(),
        type: accType,
        currency: accCurrency.trim() || 'CNY',
        initial_balance: accInitial.trim() === '' ? '0' : accInitial.trim(),
      }
      const ok = await run(() => api.createAccount(payload), '账户已创建')
      if (ok) setAccDialog(null)
    } else {
      const ok = await run(
        () => api.updateAccount(accDialog.account.id, { name: accName.trim(), enabled: accEnabled }),
        '账户已保存',
      )
      if (ok) setAccDialog(null)
    }
  }

  // —— 分类树（两级）——
  const [catDialog, setCatDialog] = useState<
    { mode: 'create'; parentId: string | null } | { mode: 'edit'; category: Category } | null
  >(null)
  const [catName, setCatName] = useState('')
  const [catDirection, setCatDirection] = useState<'expense' | 'income'>('expense')
  const [catParent, setCatParent] = useState<string>('')
  const [catError, setCatError] = useState<string | null>(null)

  const roots = useMemo(() => categories.filter((c) => !c.parent_id), [categories])
  const childrenOf = useMemo(() => {
    const m = new Map<string, Category[]>()
    for (const c of categories) {
      if (!c.parent_id) continue
      const arr = m.get(c.parent_id) ?? []
      arr.push(c)
      m.set(c.parent_id, arr)
    }
    return m
  }, [categories])

  const openCatCreate = (parentId: string | null) => {
    setCatName('')
    setCatParent(parentId ?? '')
    setCatDirection('expense')
    setCatError(null)
    setCatDialog({ mode: 'create', parentId })
  }
  const openCatEdit = (c: Category) => {
    setCatName(c.name)
    setCatError(null)
    setCatDialog({ mode: 'edit', category: c })
  }

  const submitCategory = async () => {
    if (!catDialog) return
    if (catName.trim() === '') {
      setCatError('请填写分类名称')
      return
    }
    if (catDialog.mode === 'create') {
      const parentId = catParent === '' ? null : catParent
      // R6 两级封顶：父级只能是一级分类
      const ok = await run(
        () =>
          api.createCategory({
            name: catName.trim(),
            direction: catDirection,
            ...(parentId ? { parent_id: parentId } : {}),
          }),
        '分类已创建',
      )
      if (ok) setCatDialog(null)
    } else {
      const ok = await run(
        () => api.updateCategory(catDialog.category.id, { name: catName.trim() }),
        '分类已保存',
      )
      if (ok) setCatDialog(null)
    }
  }

  const toggleCategory = (c: Category) => {
    void run(() => api.updateCategory(c.id, { enabled: !c.enabled }), c.enabled ? '已停用' : '已启用')
  }

  // —— 标签管理（仅重命名：创建走记一笔 get-or-create，无删除）——
  const [tagEdit, setTagEdit] = useState<{ id: string; name: string } | null>(null)
  const [tagError, setTagError] = useState<string | null>(null)

  const submitTagRename = async () => {
    if (!tagEdit) return
    if (tagEdit.name.trim() === '') {
      setTagError('标签名不能为空')
      return
    }
    const ok = await run(
      () => api.renameTag(tagEdit.id, { name: tagEdit.name.trim() }),
      '标签已重命名',
    )
    if (ok) setTagEdit(null)
  }

  const totalBalance = sumAmounts(accounts.map((a) => a.balance))

  return (
    <div className="grid gap-4">
      {/* 次级 Tab（账户 / 分类管理 / 标签管理） */}
      <nav className="flex gap-1" role="tablist" aria-label="账户页次级导航">
        {(
          [
            { key: 'accounts', label: '账户' },
            { key: 'categories', label: '分类管理' },
            { key: 'tags', label: '标签管理' },
          ] as { key: Tab; label: string }[]
        ).map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={`btn btn-sm ${tab === t.key ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === 'accounts' && (
        <section className="grid gap-4">
          <div className="flex justify-end">
            <button className="btn btn-primary btn-sm" onClick={openAccCreate}>
              新增账户
            </button>
          </div>
          {accounts.length === 0 ? (
            <div className="card bg-base-100 shadow">
              <div className="card-body py-6 opacity-60 text-sm">
                还没有账户，点右上「新增账户」开始
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {accounts.map((a) => (
                <div
                  key={a.id}
                  className="card bg-base-100 shadow cursor-pointer hover:shadow-md transition-shadow"
                  onClick={() => onJumpAccount({ accountId: a.id })}
                >
                  <div className="card-body py-4 grid gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-medium" title="点击卡片查看该账户流水">
                        {a.name}
                      </span>
                      <span className="badge orz-tag badge-sm">{TYPE_LABEL[a.type]}</span>
                      {!a.enabled && <span className="badge badge-ghost badge-sm">已停用</span>}
                      <div className="flex-1" />
                      <button
                        className="btn btn-ghost btn-xs"
                        onClick={(e) => {
                          e.stopPropagation()
                          openAccEdit(a)
                        }}
                      >
                        编辑
                      </button>
                    </div>
                    <div className="text-right text-xl tabular-nums">{thousands(a.balance)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          {/* 合计行（资产合计=Σ各账户余额；前端求和口径待议项已列 TL——备选=移除合计行） */}
          {accounts.length > 0 && (
            <div className="card bg-base-100 shadow">
              <div className="card-body py-4 flex items-center justify-between">
                <span className="text-sm opacity-60">资产合计（Σ各账户余额，R3 对冲不虚增）</span>
                <span className="text-lg tabular-nums">{thousands(totalBalance)}</span>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === 'categories' && (
        <section className="grid gap-4">
          <div className="flex justify-end">
            <button className="btn btn-primary btn-sm" onClick={() => openCatCreate(null)}>
              新增一级分类
            </button>
          </div>
          <div className="card bg-base-100 shadow">
            <div className="card-body py-4 grid gap-1">
              {roots.length === 0 && (
                <div className="opacity-60 text-sm py-2">暂无分类</div>
              )}
              {roots.map((c) => (
                <div key={c.id} className="grid gap-1">
                  <div className="flex items-center gap-2 py-1">
                    <span className="badge badge-ghost badge-sm">
                      {c.direction === 'expense' ? '支出' : '收入'}
                    </span>
                    <span className={!c.enabled ? 'opacity-50' : ''}>{c.name}</span>
                    {c.is_preset && <span className="badge orz-tag badge-sm">预置</span>}
                    <div className="flex-1" />
                    <input
                      type="checkbox"
                      className="toggle toggle-xs"
                      aria-label={c.enabled ? '停用分类' : '启用分类'}
                      checked={c.enabled}
                      disabled={busy}
                      onChange={() => toggleCategory(c)}
                    />
                    <button className="btn btn-ghost btn-xs" onClick={() => openCatEdit(c)}>
                      编辑
                    </button>
                    <button
                      className="btn btn-ghost btn-xs"
                      onClick={() => openCatCreate(c.id)}
                    >
                      +子分类
                    </button>
                  </div>
                  {(childrenOf.get(c.id) ?? []).map((ch) => (
                    <div key={ch.id} className="flex items-center gap-2 py-1 pl-8">
                      <span className="opacity-40">└</span>
                      <span className={!ch.enabled ? 'opacity-50' : ''}>{ch.name}</span>
                      {ch.is_preset && <span className="badge orz-tag badge-sm">预置</span>}
                      <div className="flex-1" />
                      <input
                        type="checkbox"
                        className="toggle toggle-xs"
                        aria-label={ch.enabled ? '停用分类' : '启用分类'}
                        checked={ch.enabled}
                        disabled={busy}
                        onChange={() => toggleCategory(ch)}
                      />
                      <button className="btn btn-ghost btn-xs" onClick={() => openCatEdit(ch)}>
                        编辑
                      </button>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
          <p className="text-xs opacity-50">
            预置分类不可删除，仅可停用；停用后不再出现在记一笔与筛选下拉中。
          </p>
        </section>
      )}

      {tab === 'tags' && (
        <section className="grid gap-4">
          <p className="text-xs opacity-50">
            标签在「记一笔」输入即创建（get-or-create）；此处支持重命名，无删除。
          </p>
          <div className="card bg-base-100 shadow">
            <div className="card-body py-4 grid gap-1">
              {tags.length === 0 && <div className="opacity-60 text-sm py-2">暂无标签</div>}
              {tags.map((t) => (
                <div key={t.id} className="flex items-center gap-2 py-1">
                  <span className="badge badge-ghost">{t.name}</span>
                  <div className="flex-1" />
                  <button
                    className="btn btn-ghost btn-xs"
                    onClick={() => {
                      setTagEdit({ id: t.id, name: t.name })
                      setTagError(null)
                    }}
                  >
                    重命名
                  </button>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* 新增/编辑账户弹窗（不做删除；编辑可改名称与启停） */}
      {accDialog && (
        <dialog className="modal modal-open" data-testid="account-modal">
          <div className="modal-box max-w-sm">
            <h3 className="font-semibold text-lg mb-3">
              {accDialog.mode === 'create' ? '新增账户' : '编辑账户'}
            </h3>
            <div className="grid gap-3">
              <label className="form-control">
                <div className="label py-1">
                  <span className="label-text">名称</span>
                </div>
                <input
                  className="input input-bordered input-sm"
                  value={accName}
                  onChange={(e) => setAccName(e.target.value)}
                  autoFocus
                />
              </label>
              {accDialog.mode === 'create' ? (
                <>
                  <label className="form-control">
                    <div className="label py-1">
                      <span className="label-text">类型</span>
                    </div>
                    <select
                      className="select select-bordered select-sm"
                      value={accType}
                      onChange={(e) => setAccType(e.target.value as Account['type'])}
                    >
                      <option value="cash">现金</option>
                      <option value="debit">借记卡</option>
                      <option value="credit">信用卡</option>
                      <option value="virtual">虚拟账户</option>
                    </select>
                  </label>
                  <label className="form-control">
                    <div className="label py-1">
                      <span className="label-text">币种（默认本位币）</span>
                    </div>
                    <input
                      className="input input-bordered input-sm"
                      value={accCurrency}
                      onChange={(e) => setAccCurrency(e.target.value)}
                      placeholder="CNY"
                    />
                  </label>
                  <label className="form-control">
                    <div className="label py-1">
                      <span className="label-text">初始余额</span>
                    </div>
                    <input
                      type="number"
                      className="input input-bordered input-sm"
                      value={accInitial}
                      onChange={(e) => setAccInitial(e.target.value)}
                      step="0.01"
                    />
                  </label>
                </>
              ) : (
                <label className="label cursor-pointer justify-start gap-3 py-1">
                  <input
                    type="checkbox"
                    className="toggle toggle-sm"
                    checked={accEnabled}
                    onChange={(e) => setAccEnabled(e.target.checked)}
                  />
                  <span className="label-text">启用该账户</span>
                </label>
              )}
              {accError && <p className="text-error text-sm">{accError}</p>}
            </div>
            <div className="modal-action">
              <button className="btn btn-ghost" disabled={busy} onClick={() => setAccDialog(null)}>
                取消
              </button>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void submitAccount()}
              >
                {busy && <span className="loading loading-spinner loading-xs" />}
                保存
              </button>
            </div>
          </div>
          <button className="modal-backdrop" onClick={() => setAccDialog(null)} aria-label="关闭" />
        </dialog>
      )}

      {/* 新增/编辑分类弹窗（R6 两级封顶：父级仅一级分类可选） */}
      {catDialog && (
        <dialog className="modal modal-open" data-testid="category-modal">
          <div className="modal-box max-w-sm">
            <h3 className="font-semibold text-lg mb-3">
              {catDialog.mode === 'create'
                ? catParent
                  ? '新增子分类'
                  : '新增一级分类'
                : '编辑分类'}
            </h3>
            <div className="grid gap-3">
              <label className="form-control">
                <div className="label py-1">
                  <span className="label-text">名称</span>
                </div>
                <input
                  className="input input-bordered input-sm"
                  value={catName}
                  onChange={(e) => setCatName(e.target.value)}
                  autoFocus
                />
              </label>
              {catDialog.mode === 'create' && (
                <>
                  {!catParent && (
                    <label className="form-control">
                      <div className="label py-1">
                        <span className="label-text">收支方向</span>
                      </div>
                      <select
                        className="select select-bordered select-sm"
                        value={catDirection}
                        onChange={(e) =>
                          setCatDirection(e.target.value as 'expense' | 'income')
                        }
                      >
                        <option value="expense">支出</option>
                        <option value="income">收入</option>
                      </select>
                    </label>
                  )}
                  <label className="form-control">
                    <div className="label py-1">
                      <span className="label-text">父级分类（可选，仅一级）</span>
                    </div>
                    <select
                      className="select select-bordered select-sm"
                      value={catParent}
                      onChange={(e) => setCatParent(e.target.value)}
                    >
                      <option value="">（无——作为一级分类）</option>
                      {roots
                        .filter((r) => r.direction === catDirection)
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                    </select>
                  </label>
                </>
              )}
              {catError && <p className="text-error text-sm">{catError}</p>}
            </div>
            <div className="modal-action">
              <button className="btn btn-ghost" disabled={busy} onClick={() => setCatDialog(null)}>
                取消
              </button>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void submitCategory()}
              >
                {busy && <span className="loading loading-spinner loading-xs" />}
                保存
              </button>
            </div>
          </div>
          <button className="modal-backdrop" onClick={() => setCatDialog(null)} aria-label="关闭" />
        </dialog>
      )}

      {/* 标签重命名弹窗 */}
      {tagEdit && (
        <dialog className="modal modal-open" data-testid="tag-rename-modal">
          <div className="modal-box max-w-xs">
            <h3 className="font-semibold text-lg mb-3">重命名标签</h3>
            <input
              className="input input-bordered input-sm w-full"
              value={tagEdit.name}
              onChange={(e) => setTagEdit({ ...tagEdit, name: e.target.value })}
              autoFocus
            />
            {tagError && <p className="text-error text-sm mt-2">{tagError}</p>}
            <div className="modal-action">
              <button className="btn btn-ghost" disabled={busy} onClick={() => setTagEdit(null)}>
                取消
              </button>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void submitTagRename()}
              >
                {busy && <span className="loading loading-spinner loading-xs" />}
                保存
              </button>
            </div>
          </div>
          <button className="modal-backdrop" onClick={() => setTagEdit(null)} aria-label="关闭" />
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
