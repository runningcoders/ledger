/** 日期/时间展示辅助（纯展示层，时区取本地） */

/** ISO 时间字符串 → 'HH:mm' */
export function hhmm(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

/** ISO 时间字符串 → 'YYYY-MM-DD'（本地日） */
export function dayKey(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 'YYYY-MM-DD' → '10月14日 周二'（UI 规范 §3.2 分组头口径） */
export function groupTitle(day: string): string {
  const d = new Date(`${day}T00:00:00`)
  if (Number.isNaN(d.getTime())) return day
  return `${d.getMonth() + 1}月${d.getDate()}日 ${WEEK[d.getDay()]}`
}

/** datetime-local 输入框值 ↔ ISO：展示/回填用 */
export function toDatetimeLocal(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 'YYYY-MM-DDTHH:mm' → 后端可接受时间（缺秒补 :00） */
export function fromDatetimeLocal(v: string): string {
  return v.length === 16 ? `${v}:00` : v
}

/** 快捷项：今天/昨天 全天（UI 规范 §3.3 时间快捷项） */
export function quickRange(kind: 'today' | 'yesterday'): { start: string; end: string } {
  const now = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  const dayStr = (d: Date) => `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
  if (kind === 'today') {
    return { start: `${dayStr(now)}T00:00`, end: `${dayStr(now)}T23:59` }
  }
  const y = new Date(now.getTime() - 24 * 3600 * 1000)
  return { start: `${dayStr(y)}T00:00`, end: `${dayStr(y)}T23:59` }
}
