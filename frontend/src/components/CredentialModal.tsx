/**
 * 「解锁写入」凭证弹窗（总管模式评估面 c，AMan 拍板③按推荐）。
 *
 * 交互口径：用户在终端按 README 口径构造结构化 base64 凭证串（echo ... | base64，
 * 占位符 <你的UID>），粘贴解锁——凭证仅驻内存 + sessionStorage（会话级，不落
 * localStorage）。后端 401 时 request() 单点已清态并派发 ledger:credential-required
 * 事件，顶栏监听自动重弹本弹窗（失败不静默）。
 */
import { useEffect, useState } from 'react'
import { clearCredential, getCredential, setCredential } from '../api/model'

interface Props {
  open: boolean
  onClose: () => void
  /** 解锁成功回调（顶栏刷新解锁徽标） */
  onUnlocked: () => void
}

export default function CredentialModal({ open, onClose, onUnlocked }: Props) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const hasCredential = getCredential() !== null

  // 清空输入态：每次打开都从空白开始（不回显已存凭证——零回显口径）
  useEffect(() => {
    if (open) {
      setValue('')
      setError(null)
    }
  }, [open])

  if (!open) return null

  const submit = () => {
    const v = value.trim()
    if (!v) {
      setError('请粘贴凭证串')
      return
    }
    // 前端预检（与后端六步链第一/二步同构：非空 + base64 字符集粗校验；
    // 最终以服务端解析为准——401 由 request() 统一清态重弹）
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(v)) {
      setError('凭证须为 base64 串（echo \'{"v":1,"user_id":"<你的UID>"}\' | base64 的输出）')
      return
    }
    setCredential(v)
    onUnlocked()
    onClose()
  }

  const forget = () => {
    clearCredential()
    onUnlocked()
    onClose()
  }

  return (
    <dialog className="modal modal-open" data-testid="credential-modal">
      <div className="modal-box max-w-md">
        <h3 className="font-semibold text-lg mb-1">解锁写入</h3>
        <p className="text-xs opacity-70 mb-3">
          总管模式为只读形态：写操作需粘贴请求级凭证（结构化 base64
          串，写目标由凭证解析，不经 URL）。凭证仅驻本会话（内存 +
          sessionStorage），浏览器关闭即清，不上传任何服务器之外的本地服务。
        </p>
        <input
          data-testid="credential-input"
          type="password"
          className={`input input-bordered w-full font-mono text-xs ${error ? 'input-error' : ''}`}
          placeholder="粘贴 base64 凭证串（echo '{&quot;v&quot;:1,&quot;user_id&quot;:&quot;<你的UID>&quot;}' | base64）"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
        />
        {error && <p className="text-error text-xs mt-1">{error}</p>}
        <div className="modal-action">
          {hasCredential && (
            <button className="btn btn-ghost" onClick={forget}>
              清除已存凭证
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" data-testid="credential-submit" onClick={submit}>
            解锁
          </button>
        </div>
      </div>
      <button className="modal-backdrop" onClick={onClose} aria-label="关闭" />
    </dialog>
  )
}
