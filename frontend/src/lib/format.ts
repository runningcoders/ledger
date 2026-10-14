/**
 * 金额排版全局铁律（UI 规范 §1.3）：
 * tabular-nums 等宽数字 + 右对齐 + 千分位 + 两位小数 + 单币种不显币符。
 * 收入=正绿，支出=负红，结余/余额=中性，转账=中性零着色。
 */

export type AmountKind = 'income' | 'expense' | 'neutral'

/** 原样透传字符串（单一事实源为后端，前端零金额算术——铁律） */
export function amountText(amount: string): string {
  return amount
}

/** 前缀：收入 +、支出 −、其余无 */
export function amountSign(kind: AmountKind): string {
  if (kind === 'income') return '+'
  if (kind === 'expense') return '−'
  return ''
}

/** 语义色 class（仅引用 HUD 既有令牌档位，零新增） */
export function amountColorClass(kind: AmountKind): string {
  if (kind === 'income') return 'text-success'
  if (kind === 'expense') return 'text-error'
  return ''
}

/** 整数部分千分位（仅展示层；两位小数由后端契约保证，此处不重排小数） */
export function thousands(amount: string): string {
  const neg = amount.startsWith('-')
  const body = neg ? amount.slice(1) : amount
  const [int, frac] = body.split('.')
  const intFmt = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return (neg ? '-' : '') + (frac ? `${intFmt}.${frac}` : intFmt)
}

/** 由交易类型推导金额语义（转账与不计收支由调用方覆盖为 neutral） */
export function kindOf(
  type: string,
  inSum: boolean,
): AmountKind {
  if (!inSum) return 'neutral'
  if (type === 'income') return 'income'
  if (type === 'expense') return 'expense'
  return 'neutral'
}
