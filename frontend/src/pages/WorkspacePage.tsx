import { useEffect, useRef, useState } from 'react'

import type { Connection } from '../api/types'
import { ClipboardBridge } from '../components/workspace/ClipboardBridge'
import { ConnectionDialog } from '../components/workspace/ConnectionDialog'
import { ConnectionList } from '../components/workspace/ConnectionList'
import { EmptyWorkspace } from '../components/workspace/EmptyWorkspace'
import { FilePanel } from '../components/workspace/FilePanel'
import { PromptDialogs } from '../components/workspace/PromptDialogs'
import { SessionTabs } from '../components/workspace/SessionTabs'
import { TerminalHost } from '../components/workspace/TerminalHost'
import { peekTerminal } from '../lib/terminalCache'
import { useWorkspace } from '../state/workspace'

const CARD = 'rounded-2xl border border-ink-700 bg-ink-850/80 shadow-2xl shadow-black/30 backdrop-blur'

/** Below md there is no docked list, there it always floats. */
function useWide(): boolean {
  const query = '(min-width: 768px)'
  const [wide, setWide] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setWide(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return wide
}

/**
 * The workspace in the centered column like the other pages: connections as a card on the left,
 * terminal as a card on the right. Collapsed, the list floats over the terminal.
 */
export function WorkspacePage() {
  const { sessions, activeId, listCollapsed } = useWorkspace()
  const wide = useWide()
  const docked = wide && !listCollapsed
  // undefined: no dialog, connection null: new connection.
  const [editing, setEditing] = useState<{ connection: Connection | null; share: boolean } | undefined>(undefined)
  const edit = (connection: Connection, share = false) => setEditing({ connection, share })
  const create = () => setEditing({ connection: null, share: false })
  const [floatingOpen, setFloatingOpen] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const cardRef = useRef<HTMLElement>(null)
  const active = sessions.find((s) => s.id === activeId)

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === cardRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // If the list is docked again, the floating one is no longer needed.
  useEffect(() => {
    if (docked) setFloatingOpen(false)
  }, [docked])

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void cardRef.current?.requestFullscreen()
  }

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      {docked && <ConnectionList onEdit={edit} onNew={create} />}

      <section ref={cardRef} className={'relative flex min-w-0 flex-1 flex-col overflow-hidden ' + (fullscreen ? 'bg-ink-950' : CARD)}>
        {(sessions.length > 0 || !docked) && (
          <SessionTabs
            showListButton={!docked}
            listOpen={floatingOpen}
            onToggleList={() => setFloatingOpen((open) => !open)}
            onReconnect={(id) => peekTerminal(id)?.reconnect()}
            fullscreen={fullscreen}
            onToggleFullscreen={toggleFullscreen}
          />
        )}
        {sessions.length === 0 ? (
          <EmptyWorkspace onNew={create} onOpenList={docked ? undefined : () => setFloatingOpen(true)} />
        ) : (
          <div className="flex min-h-0 flex-1">
            <div className={'min-w-0 flex-1 flex-col ' + (active?.filesOpen ? 'hidden md:flex' : 'flex')}>
              {sessions.map((session) => (
                <TerminalHost key={session.id} session={session} visible={session.id === activeId} />
              ))}
            </div>
            {active?.filesOpen && active.serverId && <FilePanel key={active.id} session={active} />}
          </div>
        )}

        {!docked && floatingOpen && (
          <>
            <div className="absolute inset-0 z-20 bg-ink-950/40" onClick={() => setFloatingOpen(false)} aria-hidden="true" />
            <div className="pointer-events-none absolute top-14 bottom-10 left-3 z-30 flex items-start">
              <ConnectionList floating onEdit={edit} onNew={create} onPicked={() => setFloatingOpen(false)} onClose={() => setFloatingOpen(false)} />
            </div>
          </>
        )}
      </section>

      <PromptDialogs />
      <ClipboardBridge />
      {editing && <ConnectionDialog connection={editing.connection} startTab={editing.share ? 'share' : 'basics'} onClose={() => setEditing(undefined)} />}
    </div>
  )
}
