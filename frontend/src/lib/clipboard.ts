/**
 * The clipboard. Writing works almost always, falling back to the old way with
 * execCommand if needed. Reading is only allowed by the browser in a secure context (HTTPS or
 * localhost) and usually only after a confirmation prompt. Ctrl+V needs neither, because
 * there the browser pastes by itself.
 */

export async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Continue with the old way.
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  const active = document.activeElement as HTMLElement | null
  area.select()
  let ok: boolean
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  area.remove()
  active?.focus()
  return ok
}

export type ReadResult = { ok: true; text: string } | { ok: false; reason: 'insecure' | 'denied' }

export async function readClipboard(): Promise<ReadResult> {
  if (!navigator.clipboard?.readText || !window.isSecureContext) return { ok: false, reason: 'insecure' }
  try {
    return { ok: true, text: await navigator.clipboard.readText() }
  } catch {
    return { ok: false, reason: 'denied' }
  }
}
