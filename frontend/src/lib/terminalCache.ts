/**
 * One terminal per session, across page switches. React is allowed to tear down the workspace (for example when
 * switching to the vault); the terminal, along with its history and WebSocket, stays here and gets
 * remounted when coming back.
 *
 * The WebSocket to the server carries bytes in both directions (keyboard in, output back) and, alongside
 * that, short JSON messages: status, prompts (host key, password, passphrase) and size.
 */

import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'

import { readClipboard, writeClipboard } from './clipboard'
import { terminalPrefs } from './terminalPrefs'
import { terminalTheme } from './terminalTheme'
import { THEME_EVENT } from './theme'

/** Events to the UI: copied, confirmation prompt before pasting, clipboard blocked. */
export const COPIED_EVENT = 'nextrmnl-copied'
export const PASTE_CONFIRM_EVENT = 'nextrmnl-paste-confirm'
export const CLIPBOARD_NOTE_EVENT = 'nextrmnl-clipboard-note'

export type CopiedDetail = { sessionId: string; chars: number; ok: boolean }
export type PasteConfirmDetail = { sessionId: string; text: string }
export type ClipboardNoteDetail = { reason: 'insecure' | 'denied' }

function emit<T>(name: string, detail: T): void {
  window.dispatchEvent(new CustomEvent<T>(name, { detail }))
}

/** Lines of a pasted text; a single trailing line break does not count. */
export function pastedLines(text: string): string[] {
  return text.replace(/(\r\n|\r|\n)$/, '').split(/\r\n|\r|\n/)
}

export type ConnectStatus = 'connecting' | 'open' | 'closed' | 'failed'

export interface Quick {
  host: string
  port: number
  user: string
}

export interface Target {
  connectionId: number | null
  quick: Quick | null
  /** For the messages in the terminal. */
  label: string
}

export interface StatusInfo {
  state: ConnectStatus
  serverId?: string
  detail?: string
  via?: string
  jump?: string | null
  latencyMs?: number | null
}

export type PromptKind = 'hostkey-new' | 'hostkey-changed' | 'password' | 'passphrase'

export interface PromptData {
  host?: string
  port?: number
  keyType?: string
  fingerprint?: string
  storedFingerprint?: string
  /** user@host for password, key name for passphrase. */
  target?: string
  /** Second attempt: what was entered was rejected. */
  retry?: boolean
  /** The stored password did not work. */
  storedFailed?: boolean
  /** The server allows storing the password in the vault. */
  canStore?: boolean
}

export type PromptAnswer = { accept: boolean } | { value: string; store?: boolean }

export interface TerminalTexts {
  connecting: (target: string) => string
  connected: (how: string) => string
  failed: (detail: string) => string
  closed: string
  reconnectHint: string
}

export interface Handlers {
  onStatus: (info: StatusInfo) => void
  onPrompt: (kind: PromptKind, data: PromptData) => void
}

interface Entry {
  term: Terminal
  fit: FitAddon
  element: HTMLDivElement
  status: ConnectStatus
  serverId: string | null
  socket: WebSocket | null
  opened: boolean
  reconnect: () => void
  answer: (message: Record<string, unknown>) => void
  offTheme: () => void
}

const cache = new Map<string, Entry>()

const DIM = '\x1b[90m'
const RED = '\x1b[31m'
const RESET = '\x1b[0m'
const encoder = new TextEncoder()

function socketUrl(target: Target, cols: number, rows: number): string {
  const params = new URLSearchParams({ cols: String(cols), rows: String(rows) })
  if (target.connectionId !== null) params.set('connection_id', String(target.connectionId))
  else if (target.quick) {
    params.set('host', target.quick.host)
    params.set('port', String(target.quick.port))
    params.set('user', target.quick.user)
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}/api/sessions/ws?${params.toString()}`
}

export function getTerminal(sessionId: string, target: Target, texts: TerminalTexts, handlers: Handlers): Entry {
  const existing = cache.get(sessionId)
  if (existing) return existing

  const prefs = terminalPrefs()
  const term = new Terminal({
    fontFamily: getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim() || 'monospace',
    fontSize: prefs.fontSize,
    lineHeight: 1.2,
    cursorBlink: prefs.cursorBlink,
    cursorStyle: prefs.cursorStyle,
    scrollback: prefs.scrollback,
    theme: terminalTheme(),
    allowProposedApi: false,
    // Without dot, colon, slash and @: a double-click takes IP addresses, paths and user@host whole.
    wordSeparator: ` ()[]{}'",;|<>`,
  })
  const fit = new FitAddon()
  term.loadAddon(fit)
  const element = document.createElement('div')
  element.className = 'h-full w-full'

  const entry: Entry = {
    term,
    fit,
    element,
    status: 'connecting',
    serverId: null,
    socket: null,
    opened: false,
    reconnect: () => {},
    answer: () => {},
    offTheme: () => {},
  }

  const setStatus = (info: StatusInfo) => {
    entry.status = info.state
    if (info.serverId) entry.serverId = info.serverId
    handlers.onStatus(info)
  }

  const send = (message: Record<string, unknown>) => {
    if (entry.socket?.readyState === WebSocket.OPEN) entry.socket.send(JSON.stringify(message))
  }

  const handleControl = (raw: string) => {
    let message: Record<string, unknown>
    try {
      message = JSON.parse(raw) as Record<string, unknown>
    } catch {
      return
    }
    const type = message.type
    if (type === 'status') {
      const state = String(message.state) as ConnectStatus
      const detail = message.detail ? String(message.detail) : undefined
      if (state === 'open') {
        term.write(`${DIM}${texts.connected(String(message.via ?? ''))}${RESET}\r\n\r\n`)
        // The size is only known now, the server should know it.
        send({ type: 'resize', cols: term.cols, rows: term.rows })
      } else if (state === 'failed') {
        term.write(`${RED}${texts.failed(detail ?? '')}${RESET}\r\n${DIM}${texts.reconnectHint}${RESET}\r\n`)
      } else if (state === 'closed') {
        // An end that did not come from the shell (operator, server restart) gets its reason added.
        const why = detail && detail !== 'exit' && detail !== 'browser_closed' ? ` ${texts.failed(detail)}` : ''
        term.write(`\r\n${DIM}${texts.closed}${why} ${texts.reconnectHint}${RESET}\r\n`)
      }
      setStatus({
        state,
        serverId: message.session_id ? String(message.session_id) : undefined,
        detail,
        via: message.via ? String(message.via) : undefined,
        jump: message.jump === undefined ? undefined : (message.jump as string | null),
        latencyMs: message.latency_ms === undefined ? undefined : (message.latency_ms as number | null),
      })
      return
    }
    if (type === 'hostkey') {
      const stored = message.old_fingerprint ?? message.stored_fingerprint
      handlers.onPrompt(message.state === 'changed' ? 'hostkey-changed' : 'hostkey-new', {
        host: String(message.host ?? ''),
        port: Number(message.port ?? 22),
        keyType: String(message.key_type ?? ''),
        fingerprint: String(message.fingerprint ?? ''),
        storedFingerprint: stored ? String(stored) : undefined,
      })
      return
    }
    if (type === 'need') {
      const passphrase = message.what === 'passphrase'
      handlers.onPrompt(passphrase ? 'passphrase' : 'password', {
        target: passphrase ? String(message.key ?? '') : `${String(message.user ?? '')}@${String(message.host ?? target.label)}`,
        retry: Boolean(message.retry),
        storedFailed: Boolean(message.stored_failed),
        canStore: Boolean(message.can_store),
      })
      return
    }
    if (type === 'error') {
      term.write(`${RED}${String(message.message ?? message.code ?? '')}${RESET}\r\n`)
    }
  }

  const connect = () => {
    entry.socket?.close()
    entry.socket = null
    entry.serverId = null
    setStatus({ state: 'connecting' })
    term.write(`${DIM}${texts.connecting(target.label)}${RESET}\r\n`)
    const socket = new WebSocket(socketUrl(target, term.cols, term.rows))
    socket.binaryType = 'arraybuffer'
    entry.socket = socket
    socket.onmessage = (event) => {
      if (typeof event.data === 'string') handleControl(event.data)
      else term.write(new Uint8Array(event.data as ArrayBuffer))
    }
    socket.onclose = (event) => {
      if (entry.socket !== socket) return
      entry.socket = null
      if (entry.status === 'open') {
        term.write(`\r\n${DIM}${texts.closed} ${texts.reconnectHint}${RESET}\r\n`)
        setStatus({ state: 'closed' })
      } else if (entry.status === 'connecting') {
        const detail = event.code === 4401 ? 'not_signed_in' : 'connection_lost'
        term.write(`${RED}${texts.failed(detail)}${RESET}\r\n${DIM}${texts.reconnectHint}${RESET}\r\n`)
        setStatus({ state: 'failed', detail })
      }
    }
    socket.onerror = () => {
      /* onclose follows and reports the state. */
    }
  }

  // A line separates the old session from the new one. Without it, two logins stood one below the other.
  entry.reconnect = () => {
    term.write(`\r\n${DIM}${'─'.repeat(Math.min(term.cols, 60))}${RESET}\r\n`)
    connect()
  }
  entry.answer = send

  const copySelection = async (): Promise<void> => {
    const text = term.getSelection()
    if (!text) return
    const ok = await writeClipboard(text)
    emit<CopiedDetail>(COPIED_EVENT, { sessionId, chars: text.length, ok })
  }

  // A single line without a trailing break, so it does not run immediately. Multiple lines only after confirmation.
  const pasteText = (text: string) => {
    if (!text) return
    const lines = pastedLines(text)
    if (lines.length > 1 && terminalPrefs().confirmMultiline) {
      emit<PasteConfirmDetail>(PASTE_CONFIRM_EVENT, { sessionId, text })
      return
    }
    term.paste(lines.length > 1 ? text : lines[0])
  }

  term.attachCustomKeyEventHandler((event) => {
    if (event.type !== 'keydown') return true
    const key = event.key.toLowerCase()
    const copyKey = (event.ctrlKey && event.shiftKey && key === 'c') || (event.ctrlKey && key === 'insert') || (event.metaKey && key === 'c')
    if (copyKey) {
      event.preventDefault()
      void copySelection()
      return false
    }
    // Ctrl+C with a selection copies. Without a selection it goes to the shell as usual, as an interrupt.
    if (event.ctrlKey && !event.shiftKey && !event.altKey && key === 'c' && term.hasSelection()) {
      event.preventDefault()
      void copySelection().then(() => term.clearSelection())
      return false
    }
    // Pasting is done by the browser itself; the paste event is caught further down by the frame.
    if ((event.ctrlKey && key === 'v') || (event.metaKey && key === 'v') || (event.shiftKey && key === 'insert')) return false
    return true
  })

  element.addEventListener(
    'paste',
    (event) => {
      event.preventDefault()
      event.stopImmediatePropagation()
      pasteText(event.clipboardData?.getData('text/plain') ?? '')
    },
    true,
  )

  element.addEventListener('mouseup', () => {
    // The selection is only final on the next tick.
    window.setTimeout(() => {
      if (terminalPrefs().copyOnSelect && term.hasSelection()) void copySelection()
    }, 0)
  })

  element.addEventListener('contextmenu', (event) => {
    if (!terminalPrefs().rightClick) return
    event.preventDefault()
    term.focus()
    if (term.hasSelection()) {
      void copySelection().then(() => term.clearSelection())
      return
    }
    void readClipboard().then((result) => {
      if (result.ok) pasteText(result.text)
      else emit<ClipboardNoteDetail>(CLIPBOARD_NOTE_EVENT, { reason: result.reason })
    })
  })

  term.onData((data) => {
    if (entry.status === 'open' && entry.socket?.readyState === WebSocket.OPEN) entry.socket.send(encoder.encode(data))
    // Like PuTTY: after the end, Enter restarts the session.
    else if ((entry.status === 'closed' || entry.status === 'failed') && data === '\r') entry.reconnect()
  })

  term.onResize(({ cols, rows }) => {
    if (entry.status === 'open') send({ type: 'resize', cols, rows })
  })

  const onTheme = () => {
    term.options.theme = terminalTheme()
  }
  window.addEventListener(THEME_EVENT, onTheme)
  entry.offTheme = () => window.removeEventListener(THEME_EVENT, onTheme)

  cache.set(sessionId, entry)
  connect()
  return entry
}

export function peekTerminal(sessionId: string): Entry | undefined {
  return cache.get(sessionId)
}

export function disposeTerminal(sessionId: string): void {
  const entry = cache.get(sessionId)
  if (!entry) return
  const socket = entry.socket
  entry.socket = null
  socket?.close()
  entry.offTheme()
  entry.term.dispose()
  entry.element.remove()
  cache.delete(sessionId)
}

export function disposeAllTerminals(): void {
  for (const id of [...cache.keys()]) disposeTerminal(id)
}
