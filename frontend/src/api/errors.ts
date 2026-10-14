/** 统一错误与响应辅助：消费后端 {error:{code,message}} 错误契约 */

/** 从 fetch 响应提取可读错误信息（LedgerError→HTTP 状态+{error:{code,message}}；422=validation_error） */
export async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } }
    if (body && body.error && body.error.message) {
      return body.error.message
    }
  } catch {
    // 忽略非 JSON 响应体
  }
  return `请求失败（HTTP ${res.status}）`
}

export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}
