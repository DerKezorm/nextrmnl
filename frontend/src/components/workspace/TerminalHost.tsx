import '@xterm/xterm/css/xterm.css'

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { COPIED_EVENT, getTerminal, peekTerminal, type CopiedDetail } from '../../lib/terminalCache'
import { useWorkspace, type Session } from '../../state/workspace'

/** Mounts the session's terminal and keeps its size fitted. */
export function TerminalHost({ session, visible }: { session: Session; visible: boolean }) {
  const { t } = useTranslation()
  const { reportStatus, showPrompt, connections } = useWorkspace()
  const hostRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ cols: number; rows: number } | null>(null)
  const [copied, setCopied] = useState<number | null>(null)
  const connection = connections.find((c) => c.id === session.connectionId)
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
      { connectionId: session.connectionId, quick: session.quick, label },
      {
        connecting: (where) => (jump ? t('terminal.connectingJump', { target: where, jump }) : t('terminal.connecting', { target: where })),
        connected: (how) => t('terminal.connected', { how: describeVia(how) }),
        failed: (detail) => t(`terminal.fail.${detail}`, { defaultValue: t('terminal.fail.other', { detail }) }),
        closed: t('terminal.closed'),
        reconnectHint: t('terminal.reconnectHint'),
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
      entry.term.focus()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [visible, session.id, session.filesOpen])

  return (
    <div className={'min-h-0 flex-1 flex-col ' + (visible ? 'flex' : 'hidden')}>
      <div ref={hostRef} className="nt-terminal min-h-0 flex-1 overflow-hidden bg-term-bg" />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-ink-700 bg-ink-900 px-3 py-1.5 font-mono text-[11px] text-mist-500">
        {target && (
          <span>
            {target.user}@{target.host}:{target.port}
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
        {size && (
          <span className="ml-auto">
            {size.cols}×{size.rows}
          </span>
        )}
      </div>
    </div>
  )
}
