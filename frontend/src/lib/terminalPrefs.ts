/**
 * Appearance and behavior of the terminal. Stored in the browser and, when signed in, on the
 * account on the server (`/api/me/prefs`), so another device shows the same settings.
 */

export interface TerminalPrefs {
  fontSize: number
  /** A font installed on this device, for example a Nerd Font. Empty: the built-in choice of monospace fonts. */
  fontFamily: string
  /** A color scheme from `terminalSchemes`; `nex` follows light and dark mode. */
  scheme: string
  /** Ask the browser to confirm before leaving the page while a shell is open (Ctrl+W in nano). */
  warnOnClose: boolean
  cursorStyle: 'block' | 'bar' | 'underline'
  cursorBlink: boolean
  scrollback: number
  bell: 'off' | 'flash' | 'sound'
  /** Like PuTTY: whatever is selected is immediately on the clipboard. */
  copyOnSelect: boolean
  /** With a selection, right-click copies; without one, it pastes. Off: the browser's normal menu. */
  rightClick: boolean
  /** Ask before pasting multiple lines. */
  confirmMultiline: boolean
}

const KEY = 'nextrmnl.terminal'
export const PREFS_EVENT = 'nextrmnl-prefs'

export const DEFAULT_PREFS: TerminalPrefs = {
  fontSize: 14,
  fontFamily: '',
  scheme: 'nex',
  warnOnClose: true,
  cursorStyle: 'block',
  cursorBlink: true,
  scrollback: 5000,
  bell: 'flash',
  copyOnSelect: true,
  rightClick: true,
  confirmMultiline: true,
}

export function terminalPrefs(): TerminalPrefs {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<TerminalPrefs>
    return { ...DEFAULT_PREFS, ...stored }
  } catch {
    return DEFAULT_PREFS
  }
}

function store(next: TerminalPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // Then the choice only holds until the next reload.
  }
  window.dispatchEvent(new Event(PREFS_EVENT))
}

/** Font names as typed by a person: letters, digits, spaces and a few marks, nothing that ends the CSS value. */
export function cleanFontName(value: string): string {
  return value.replace(/[^\p{L}\p{N} ._+-]/gu, '').replace(/\s+/g, ' ').trim().slice(0, 64)
}

/** The CSS font stack: the chosen font first, the built-in monospace fonts as fallback. */
export function fontStack(chosen: string, builtIn: string): string {
  const name = cleanFontName(chosen)
  return name ? `"${name}", ${builtIn}` : builtIn
}

export function setTerminalPref<K extends keyof TerminalPrefs>(key: K, value: TerminalPrefs[K]): TerminalPrefs {
  const next = { ...terminalPrefs(), [key]: value }
  store(next)
  return next
}

/** Adopt settings fetched from the server, without losing the browser's choice where the server knows nothing. */
export function adoptTerminalPrefs(remote: Record<string, unknown> | undefined): void {
  if (!remote || typeof remote !== 'object') return
  const known = Object.keys(DEFAULT_PREFS) as (keyof TerminalPrefs)[]
  const picked: Partial<TerminalPrefs> = {}
  for (const key of known) {
    if (key in remote) (picked as Record<string, unknown>)[key] = remote[key]
  }
  if (Object.keys(picked).length > 0) store({ ...terminalPrefs(), ...picked })
}
