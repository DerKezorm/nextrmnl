/**
 * Access to the nextrmnl server.
 *
 * A session cookie (HttpOnly) that this script cannot read. Every mutating request carries
 * `X-Requested-By: nextrmnl`, otherwise the server rejects it (backend/app/deps.py). If the server responds with 401,
 * the client reports it upward, and the UI shows the sign-in page.
 */

import i18n from '../i18n'

let onSignedOut: (() => void) | null = null

export class ApiError extends Error {
  status: number
  code: string | null
  data: Record<string, unknown> | null

  constructor(status: number, message: string, code: string | null = null, data: Record<string, unknown> | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.data = data
  }
}

export function setSignedOutHandler(handler: (() => void) | null): void {
  onSignedOut = handler
}

/** The server only names it (`code`), the sentence is built here from `errors.byCode` in the configured language. */
export function translateError(detail: Record<string, unknown>, status: number): string {
  const code = typeof detail.code === 'string' ? detail.code : null
  const fallback = String(detail.message ?? `HTTP ${status}`)
  if (!code) return fallback
  if (code === 'internal_error') return i18n.t('errors.internal', { id: String(detail.request_id ?? '?') })
  if (code === 'too_many_attempts') {
    const seconds = Math.max(1, Math.ceil(Number(detail.retry_after ?? 1)))
    return seconds >= 60
      ? i18n.t('errors.tooManyAttemptsMinutes', { count: Math.ceil(seconds / 60) })
      : i18n.t('errors.tooManyAttemptsSeconds', { count: seconds })
  }
  const key = `errors.byCode.${code}`
  return i18n.exists(key) ? i18n.t(key, { ...detail }) : fallback
}

export function errorText(code: string | null | undefined): string {
  if (!code) return ''
  const key = `errors.byCode.${code}`
  return i18n.exists(key) ? i18n.t(key) : code
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const record = detail as Record<string, unknown>
      return new ApiError(response.status, translateError(record, response.status), typeof record.code === 'string' ? record.code : null, record)
    }
    if (typeof detail === 'string') return new ApiError(response.status, detail)
  } catch {
    /* Response was not JSON */
  }
  return new ApiError(response.status, i18n.t('errors.http', { status: response.status }))
}

export const CSRF_HEADER: Record<string, string> = { 'X-Requested-By': 'nextrmnl' }

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') Object.assign(headers, CSRF_HEADER)
  const isForm = body instanceof FormData
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, i18n.t('errors.network'), 'network')
  }
  if (response.status === 401 && !path.startsWith('/api/auth/') && !path.startsWith('/api/vault')) onSignedOut?.()
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => send<T>('GET', path),
  post: <T>(path: string, body?: unknown) => send<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => send<T>('PUT', path, body),
  delete: <T>(path: string) => send<T>('DELETE', path),
}

/** Downloads a file via POST (password in the body) and hands it to the browser to save. */
export async function downloadFile(path: string, fallbackName: string, body?: unknown): Promise<void> {
  let response: Response
  try {
    response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { ...CSRF_HEADER, 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, i18n.t('errors.network'), 'network')
  }
  if (!response.ok) throw await parseError(response)
  const disposition = response.headers.get('content-disposition') ?? ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = match?.[1] ?? fallbackName
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : i18n.t('errors.generic')
}
