import '@xterm/xterm/css/xterm.css'

import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import { COPIED_EVENT, getTerminal, peekTerminal, SEARCH_EVENT, type CopiedDetail, type SearchDetail } from '../../lib/terminalCache'
import { PREFS_EVENT } from '../../lib/terminalPrefs'
import { terminalBackground } from '../../lib/terminalTheme'
import { useWorkspace, type Session } from '../../state/workspace'
import { Symbol } from '../Symbol'
import { TerminalSearch } from './TerminalSearch'

/** Mounts the session's terminal and keeps its size fitted. */
export function TerminalHost({ session, visible, focused = visible }: { session: Session; visible: boolean; focused?: boolean }) {
  const { t } = useTranslation()
  const { reportStatus, showPrompt, connections, activate, activeId } = useWorkspace()
  const hostRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ cols: number; rows: number } | null>(null)
  const [copied, setCopied] = useState<number | null>(null)
  const [searching, setSearching] = useState(false)
  // A fixed color scheme brings its own background; the frame around the terminal takes it over.
  const [background, setBackground] = useState(terminalBackground)
  const connection = connections.find((c) => c.id === session.connectionId)

  useEffect(() => {
    const onPrefs = () => setBackground(terminalBackground())
    const onSearch = (event: Event) => {
      if ((event as CustomEvent<SearchDetail>).detail.sessionId === session.id) setSearching(true)
    }
    window.addEventListener(PREFS_EVENT, onPrefs)
    window.addEventListener(SEARCH_EVENT, onSearch)
    return () => {
      window.removeEventListener(PREFS_EVENT, onPrefs)
      window.removeEventListener(SEARCH_EVENT, onSearch)
    }
  }, [session.id])
  const target = session.quick ?? (connection ? { host: connection.host, port: connection.port, user: connection.user } : null)

  // Briefly show in the status line that something was copied. A notice in the middle would be disruptive on every selection.
  useEffect(() => {
    let timer = 0
    const onCopied = (event: Event) => {
      const detail = (event as CustomEvent<CopiedDetail>).detail
      if (detail.sessionId !== session.id || !detail.ok) return
      setCopied(detail.chars)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => setCopied(null), 1800)
    }
    window.addEventListener(COPIED_EVENT, onCopied)
    return () => {
      window.removeEventListener(COPIED_EVENT, onCopied)
      window.clearTimeout(timer)
    }
  }, [session.id])

  // The server names the method in short English ("key homelab", "stored password", "password"); here it becomes a sentence.
  const describeVia = (via: string): string => {
    if (via.startsWith('key ')) return t('terminal.viaKey', { name: via.slice(4) })
    if (via === 'stored password') return t('terminal.viaStoredPassword')
    if (via === 'password') return t('terminal.viaPassword')
    return via || t('terminal.viaUnknown')
  }

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const label = target ? `${target.user}@${target.host}:${target.port}` : session.name
    const jump = connections.find((c) => c.id === connection?.jump_id)?.name
    const entry = getTerminal(
      session.id,
      { connectionId: session.connectionId, quick: session.quick, label, resumeId: session.resumeId },
      {
        connecting: (where) => (jump ? t('terminal.connectingJump', { target: where, jump }) : t('terminal.connecting', { target: where })),
        connected: (how) => t('terminal.connected', { how: describeVia(how) }),
        failed: (detail) => t(`terminal.fail.${detail}`, { defaultValue: t('terminal.fail.other', { detail }) }),
        closed: t('terminal.closed'),
        reconnectHint: t('terminal.reconnectHint'),
        resuming: t('terminal.resuming'),
        resumed: t('terminal.resumed'),
        takenOver: t('terminal.takenOver'),
        takeBackHint: t('terminal.takeBackHint'),
      },
      {
        onStatus: (info) => reportStatus(session.id, info),
        onPrompt: (kind, data) => showPrompt(session.id, kind, data),
      },
    )
    host.appendChild(entry.element)
    if (!entry.opened) {
      entry.term.open(entry.element)
      entry.opened = true
    }
    const refit = () => {
      if (host.clientWidth === 0 || host.clientHeight === 0) return
      entry.fit.fit()
      setSize({ cols: entry.term.cols, rows: entry.term.rows })
    }
    const observer = new ResizeObserver(refit)
    observer.observe(host)
    refit()
    return () => {
      observer.disconnect()
      // Just unmount it. The terminal itself lives on in the cache.
      if (entry.element.parentElement === host) host.removeChild(entry.element)
    }
    // The session stays the same as long as the id is the same.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id])

  useEffect(() => {
    if (!visible) return
    const entry = peekTerminal(session.id)
    if (!entry) return
    // Only after rendering: before that, the field is still invisible and zero pixels in size.
    const frame = window.requestAnimationFrame(() => {
      entry.fit.fit()
      setSize({ cols: entry.term.cols, rows: entry.term.rows })
      // In the split view several terminals are visible; only the active one takes the keyboard.
      if (focused) entry.term.focus()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [visible, focused, session.id, session.filesOpen])

  return (
    <div
      className={'min-h-0 flex-1 flex-col ' + (visible ? 'flex' : 'hidden')}
      // Clicking into a field of the split view makes its session the active one, for the buttons above.
      onFocusCapture={() => {
        if (activeId !== session.id) activate(session.id)
      }}
    >
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={hostRef}
          className="nt-terminal min-h-0 flex-1 overflow-hidden bg-term-bg"
          // The frame and xterm's viewport both paint `--color-term-bg`; a fixed scheme sets it for this terminal.
          style={background ? ({ '--color-term-bg': background } as CSSProperties) : undefined}
        />
        {searching && visible && <TerminalSearch sessionId={session.id} onClose={() => setSearching(false)} />}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-ink-700 bg-ink-900 px-3 py-1.5 font-mono text-[11px] text-mist-500">
        {target && (
          <span>
            {target.user}@{target.host}:{target.port}
          </span>
        )}
        {session.status === 'connecting' && session.serverId && (
          <span className="rounded-full bg-warn-500/15 px-2 font-sans text-[11px] font-medium text-warn-500" role="status">
            {t('terminal.dropped')}
          </span>
        )}
        {session.jump && <span>{t('terminal.statusJump', { jump: session.jump })}</span>}
        {session.via && <span>{describeVia(session.via)}</span>}
        {session.latencyMs !== null && <span>{t('terminal.latency', { ms: session.latencyMs })}</span>}
        {copied !== null && (
          <span className="rounded-full bg-accent-500/15 px-2 font-sans text-[11px] font-medium text-accent-400" role="status">
            {t('clipboard.copied', { count: copied })}
          </span>
        )}
        <span className="ml-auto flex items-center gap-3">
          <button
            type="button"
            className="flex items-center gap-1 rounded px-1 text-mist-500 hover:text-mist-200"
            title={t('search.open')}
            aria-label={t('search.open')}
            onClick={() => setSearching(true)}
          >
            <Symbol name="search" className="h-3.5 w-3.5" />
          </button>
          {size && (
            <span>
              {size.cols}×{size.rows}
            </span>
          )}
        </span>
      </div>
    </div>
  )
}
