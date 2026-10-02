/**
 * One terminal per session, across page switches. React is allowed to tear down the workspace (for example when
 * switching to the vault); the terminal, along with its history and WebSocket, stays here and gets
 * remounted when coming back.
 *
 * The WebSocket to the server carries bytes in both directions (keyboard in, output back) and, alongside
 * that, short JSON messages: status, prompts (host key, password, passphrase) and size.
 *
 * A shell outlives its WebSocket for a while on the server. If the socket drops while the shell is open, the
 * terminal takes it back (`?attach=`) and says how many bytes it already has, so only the rest comes. Closing a
 * tab on purpose sends `close` first, which ends the shell at once.
 */

import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { Terminal } from '@xterm/xterm'

import { readClipboard, writeClipboard } from './clipboard'
import { fontStack, PREFS_EVENT, terminalPrefs } from './terminalPrefs'
import { terminalTheme } from './terminalTheme'
import { THEME_EVENT } from './theme'

/** Events to the UI: copied, confirmation prompt before pasting, clipboard blocked, search wanted. */
export const COPIED_EVENT = 'nextrmnl-copied'
export const PASTE_CONFIRM_EVENT = 'nextrmnl-paste-confirm'
export const CLIPBOARD_NOTE_EVENT = 'nextrmnl-clipboard-note'
export const SEARCH_EVENT = 'nextrmnl-search'

export type CopiedDetail = { sessionId: string; chars: number; ok: boolean }
export type PasteConfirmDetail = { sessionId: string; text: string }
export type ClipboardNoteDetail = { reason: 'insecure' | 'denied' }
export type SearchDetail = { sessionId: string }

/** Waits between tries to take a dropped session back; then it gives up and Enter connects anew. */
const RESUME_DELAYS = [500, 1000, 2000, 4000, 8000, 15000, 30000]

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
  /** A shell on the server to take back instead of opening a new one (after a reload). */
  resumeId?: string | null
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
  /** Taking a shell back that kept running on the server. */
  resuming: string
  resumed: string
  /** Another window took the session. */
  takenOver: string
  takeBackHint: string
}

export interface Handlers {
  onStatus: (info: StatusInfo) => void
  onPrompt: (kind: PromptKind, data: PromptData) => void
}

interface Entry {
  term: Terminal
  fit: FitAddon
  search: SearchAddon
  element: HTMLDivElement
  status: ConnectStatus
  serverId: string | null
  socket: WebSocket | null
  opened: boolean
  /** Bytes received from the current shell; a returning socket asks only for what comes after. */
  received: number
  /** The shell is still on the server (taken over by another window): Enter takes it back. */
  resumable: boolean
  reconnect: () => void
  answer: (message: Record<string, unknown>) => void
  /** Ends the shell on the server, not only the socket. */
  close: () => void
  offTheme: () => void
}

const cache = new Map<string, Entry>()

const DIM = '\x1b[90m'
const RED = '\x1b[31m'
const RESET = '\x1b[0m'
const encoder = new TextEncoder()

function socketBase(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}/api/sessions/ws`
}

function socketUrl(target: Target, cols: number, rows: number): string {
  const params = new URLSearchParams({ cols: String(cols), rows: String(rows) })
  if (target.connectionId !== null) params.set('connection_id', String(target.connectionId))
  else if (target.quick) {
    params.set('host', target.quick.host)
    params.set('port', String(target.quick.port))
    params.set('user', target.quick.user)
  }
  return `${socketBase()}?${params.toString()}`
}

function attachUrl(serverId: string, have: number | null): string {
  const params = new URLSearchParams({ attach: serverId })
  if (have !== null) params.set('have', String(have))
  return `${socketBase()}?${params.toString()}`
}

/** The built-in monospace fonts from the CSS. */
function builtInFonts(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim() || 'monospace'
}

export function getTerminal(sessionId: string, target: Target, texts: TerminalTexts, handlers: Handlers): Entry {
  const existing = cache.get(sessionId)
  if (existing) return existing

  const prefs = terminalPrefs()
  const term = new Terminal({
    fontFamily: fontStack(prefs.fontFamily, builtInFonts()),
    fontSize: prefs.fontSize,
    lineHeight: 1.2,
    cursorBlink: prefs.cursorBlink,
    cursorStyle: prefs.cursorStyle,
    scrollback: prefs.scrollback,
    theme: terminalTheme(),
    // The search marks all matches with decorations, which xterm still files under its proposed API. It opens
    // methods to this code only; nothing the shell prints can reach them.
    allowProposedApi: true,
    // Without dot, colon, slash and @: a double-click takes IP addresses, paths and user@host whole.
    wordSeparator: ` ()[]{}'",;|<>`,
  })
  const fit = new FitAddon()
  term.loadAddon(fit)
  const search = new SearchAddon()
  term.loadAddon(search)
  const element = document.createElement('div')
  element.className = 'h-full w-full'

  const entry: Entry = {
    term,
    fit,
    search,
    element,
    status: 'connecting',
    serverId: null,
    socket: null,
    opened: false,
    received: 0,
    resumable: false,
    reconnect: () => {},
    answer: () => {},
    close: () => {},
    offTheme: () => {},
  }
  let retryTimer = 0
  let retries = 0

  const setStatus = (info: StatusInfo) => {
    entry.status = info.state
    if (info.serverId) entry.serverId = info.serverId
    handlers.onStatus(info)
  }

  const send = (message: Record<string, unknown>) => {
    if (entry.socket?.readyState === WebSocket.OPEN) entry.socket.send(JSON.stringify(message))
  }

  const failAndOfferNew = (detail: string) => {
    entry.resumable = false
    term.write(`\r\n${RED}${texts.failed(detail)}${RESET}\r\n${DIM}${texts.reconnectHint}${RESET}\r\n`)
    setStatus({ state: 'failed', detail })
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
      if (state === 'open' && message.resumed) {
        retries = 0
        entry.resumable = false
        // `full`: the whole recent screen comes again, so the old one goes first. `tail`: only what was missed.
        if (message.replay === 'full') {
          term.reset()
          term.write(`${DIM}${texts.resumed}${RESET}\r\n`)
        }
        entry.received = Number(message.offset ?? 0)
        send({ type: 'resize', cols: term.cols, rows: term.rows })
      } else if (state === 'open') {
        entry.received = 0
        term.write(`${DIM}${texts.connected(String(message.via ?? ''))}${RESET}\r\n\r\n`)
        // The size is only known now, the server should know it.
        send({ type: 'resize', cols: term.cols, rows: term.rows })
      } else if (state === 'failed') {
        term.write(`${RED}${texts.failed(detail ?? '')}${RESET}\r\n${DIM}${texts.reconnectHint}${RESET}\r\n`)
      } else if (state === 'closed' && detail === 'taken_over') {
        // The shell lives on in another window. Enter takes it back here.
        entry.resumable = true
        term.write(`\r\n${DIM}${texts.takenOver} ${texts.takeBackHint}${RESET}\r\n`)
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

  const openSocket = (url: string): WebSocket => {
    window.clearTimeout(retryTimer)
    entry.socket?.close()
    const socket = new WebSocket(url)
    socket.binaryType = 'arraybuffer'
    entry.socket = socket
    socket.onmessage = (event) => {
      if (typeof event.data === 'string') {
        handleControl(event.data)
        return
      }
      const bytes = new Uint8Array(event.data as ArrayBuffer)
      entry.received += bytes.byteLength
      term.write(bytes)
    }
    socket.onerror = () => {
      /* onclose follows and reports the state. */
    }
    return socket
  }

  const connect = () => {
    entry.serverId = null
    entry.resumable = false
    retries = 0
    setStatus({ state: 'connecting' })
    term.write(`${DIM}${texts.connecting(target.label)}${RESET}\r\n`)
    const socket = openSocket(socketUrl(target, term.cols, term.rows))
    socket.onclose = (event) => {
      if (entry.socket !== socket) return
      entry.socket = null
      if (entry.status === 'open' && entry.serverId) {
        // The line to nextrmnl dropped, not the shell: it waits on the server and is taken back. Nothing is
        // written into the terminal, a full-screen program would be shifted by it; the status line says it.
        scheduleResume()
      } else if (entry.status === 'open') {
        term.write(`\r\n${DIM}${texts.closed} ${texts.reconnectHint}${RESET}\r\n`)
        setStatus({ state: 'closed' })
      } else if (entry.status === 'connecting') {
        const detail = event.code === 4401 ? 'not_signed_in' : 'connection_lost'
        term.write(`${RED}${texts.failed(detail)}${RESET}\r\n${DIM}${texts.reconnectHint}${RESET}\r\n`)
        setStatus({ state: 'failed', detail })
      }
    }
  }

  /** Takes a running shell back. `have` is null for an empty screen (after a reload): everything comes. */
  const attach = (serverId: string, have: number | null) => {
    entry.serverId = serverId
    setStatus({ state: 'connecting', serverId })
    const socket = openSocket(attachUrl(serverId, have))
    socket.onclose = (event) => {
      if (entry.socket !== socket) return
      entry.socket = null
      if (event.code === 4401) {
        failAndOfferNew('not_signed_in')
      } else if (event.code === 4404) {
        // Gone meanwhile: it ended, or nobody came back in time. Enter starts a new one.
        entry.serverId = null
        failAndOfferNew('session_gone')
      } else if (entry.status === 'open' || entry.status === 'connecting') {
        scheduleResume()
      }
    }
  }

  const scheduleResume = () => {
    const serverId = entry.serverId
    if (!serverId || retries >= RESUME_DELAYS.length) {
      // Given up for now; Enter tries once more and, if the shell is gone, starts a new one.
      failAndOfferNew('connection_lost')
      entry.resumable = Boolean(serverId)
      return
    }
    setStatus({ state: 'connecting', serverId })
    const delay = RESUME_DELAYS[retries]
    retries += 1
    window.clearTimeout(retryTimer)
    retryTimer = window.setTimeout(() => attach(serverId, entry.received), delay)
  }

  // A line separates the old session from the new one. Without it, two logins stood one below the other.
  entry.reconnect = () => {
    term.write(`\r\n${DIM}${'─'.repeat(Math.min(term.cols, 60))}${RESET}\r\n`)
    connect()
  }
  entry.answer = send
  entry.close = () => {
    window.clearTimeout(retryTimer)
    const socket = entry.socket
    entry.socket = null
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'close' }))
    socket?.close()
  }

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
    // Ctrl+Shift+F searches; Ctrl+F alone stays with the shell (one character forward in bash).
    if ((event.ctrlKey && event.shiftKey && key === 'f') || (event.metaKey && key === 'f')) {
      event.preventDefault()
      emit<SearchDetail>(SEARCH_EVENT, { sessionId })
      return false
    }
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
    // Like PuTTY: after the end, Enter restarts the session. A shell still on the server is taken back instead.
    else if ((entry.status === 'closed' || entry.status === 'failed') && data === '\r') {
      if (entry.resumable && entry.serverId) {
        retries = 0
        attach(entry.serverId, entry.received)
      } else entry.reconnect()
    }
  })

  term.onResize(({ cols, rows }) => {
    if (entry.status === 'open') send({ type: 'resize', cols, rows })
  })

  // Light or dark mode and the terminal settings apply to open terminals right away, not only to new ones.
  const onTheme = () => {
    term.options.theme = terminalTheme()
  }
  const onPrefs = () => {
    const next = terminalPrefs()
    term.options.theme = terminalTheme()
    term.options.fontFamily = fontStack(next.fontFamily, builtInFonts())
    term.options.fontSize = next.fontSize
    term.options.cursorStyle = next.cursorStyle
    term.options.cursorBlink = next.cursorBlink
    term.options.scrollback = next.scrollback
    // Another font or size changes the cell size; the number of columns follows.
    if (element.isConnected && element.clientWidth > 0) fit.fit()
  }
  window.addEventListener(THEME_EVENT, onTheme)
  window.addEventListener(PREFS_EVENT, onPrefs)
  entry.offTheme = () => {
    window.removeEventListener(THEME_EVENT, onTheme)
    window.removeEventListener(PREFS_EVENT, onPrefs)
  }

  cache.set(sessionId, entry)
  if (target.resumeId) {
    // After a reload: the shell kept running on the server and comes back with its recent screen.
    term.write(`${DIM}${texts.resuming}${RESET}\r\n`)
    attach(target.resumeId, null)
  } else connect()
  return entry
}

export function peekTerminal(sessionId: string): Entry | undefined {
  return cache.get(sessionId)
}

/** Closes a terminal for good: the shell on the server ends too, it does not wait for a return. */
export function disposeTerminal(sessionId: string): void {
  const entry = cache.get(sessionId)
  if (!entry) return
  entry.close()
  entry.offTheme()
  entry.term.dispose()
  entry.element.remove()
  cache.delete(sessionId)
}

export function disposeAllTerminals(): void {
  for (const id of [...cache.keys()]) disposeTerminal(id)
}
