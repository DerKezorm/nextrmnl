/**
 * The state of the workspace: connections from the server, open sessions (each with a WebSocket in
 * the terminal cache), the prompt that is currently open, and the vault state.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { api } from '../api/client'
import type { Connection, ReachInfo } from '../api/types'
import { useAuth } from '../auth'
import { disposeTerminal, peekTerminal, type ConnectStatus, type PromptAnswer, type PromptData, type PromptKind, type Quick, type StatusInfo } from '../lib/terminalCache'

export interface Session {
  id: string
  connectionId: number | null
  quick: Quick | null
  name: string
  status: ConnectStatus
  filesOpen: boolean
  serverId: string | null
  via: string | null
  jump: string | null
  latencyMs: number | null
  detail: string | null
}

export type Prompt = { sessionId: string; kind: PromptKind; data: PromptData }

interface WorkspaceValue {
  connections: Connection[]
  connectionsError: string | null
  reloadConnections: () => Promise<void>
  reach: Record<number, ReachInfo>
  sessions: Session[]
  activeId: string | null
  activate: (id: string) => void
  /** `candidate` for a connection that was just saved and is still missing from the state. */
  openConnection: (connectionId: number, candidate?: Connection) => void
  openQuick: (quick: Quick) => void
  closeSession: (id: string) => void
  toggleFiles: (id: string) => void
  /** Called by the terminal cache when the server reports a state. */
  reportStatus: (id: string, info: StatusInfo) => void
  showPrompt: (id: string, kind: PromptKind, data: PromptData) => void
  prompt: Prompt | null
  /** `null` means cancel: the session is closed. */
  answerPrompt: (answer: PromptAnswer | null) => void
  listCollapsed: boolean
  setListCollapsed: (collapsed: boolean) => void
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null)

let sessionCounter = 0
const PINNED_KEY = 'nextrmnl.listPinned'

/** By default the list floats over the terminal. Whoever pins it keeps that after reloading too. */
function storedPinned(): boolean {
  try {
    return localStorage.getItem(PINNED_KEY) === '1'
  } catch {
    return false
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { account, refresh } = useAuth()
  const [connections, setConnections] = useState<Connection[]>([])
  const [connectionsError, setConnectionsError] = useState<string | null>(null)
  const [reach, setReach] = useState<Record<number, ReachInfo>>({})
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [prompt, setPrompt] = useState<Prompt | null>(null)
  const [listCollapsed, setCollapsedState] = useState(() => !storedPinned())
  const connectionsRef = useRef(connections)
  connectionsRef.current = connections

  const setListCollapsed = useCallback((collapsed: boolean) => {
    setCollapsedState(collapsed)
    try {
      localStorage.setItem(PINNED_KEY, collapsed ? '0' : '1')
    } catch {
      // Then the choice only holds until the next reload.
    }
  }, [])

  const reloadConnections = useCallback(async () => {
    try {
      setConnections(await api.get<Connection[]>('/api/connections'))
      setConnectionsError(null)
    } catch (error) {
      setConnectionsError(error instanceof Error ? error.message : String(error))
    }
  }, [])

  useEffect(() => {
    void reloadConnections()
  }, [reloadConnections, account?.id])

  // Reachability every 30 seconds, only while the page is visible. Errors here are not worth a message.
  useEffect(() => {
    if (connections.length === 0) return
    let stopped = false
    const probe = async () => {
      if (document.hidden) return
      try {
        const result = await api.get<Record<string, ReachInfo>>('/api/connections/reach')
        if (!stopped) setReach(Object.fromEntries(Object.entries(result).map(([id, info]) => [Number(id), info])))
      } catch {
        /* then the last state stays */
      }
    }
    void probe()
    const timer = window.setInterval(() => void probe(), 30_000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [connections.length])

  const startSession = useCallback((connectionId: number | null, quick: Quick | null, name: string) => {
    sessionCounter += 1
    const session: Session = {
      id: `s${sessionCounter}`,
      connectionId,
      quick,
      name,
      status: 'connecting',
      filesOpen: false,
      serverId: null,
      via: null,
      jump: null,
      latencyMs: null,
      detail: null,
    }
    setSessions((current) => [...current, session])
    setActiveId(session.id)
  }, [])

  const openConnection = useCallback(
    (connectionId: number, candidate?: Connection) => {
      const connection = connectionsRef.current.find((c) => c.id === connectionId) ?? candidate
      if (!connection) return
      // If a session to it is already running, a click brings it to the front instead of starting a second one.
      const running = sessions.find((s) => s.connectionId === connectionId && (s.status === 'open' || s.status === 'connecting'))
      if (running) {
        setActiveId(running.id)
        return
      }
      startSession(connectionId, null, connection.name)
    },
    [sessions, startSession],
  )

  const openQuick = useCallback(
    (quick: Quick) => {
      startSession(null, quick, quick.host)
    },
    [startSession],
  )

  const closeSession = useCallback((id: string) => {
    disposeTerminal(id)
    setPrompt((current) => (current?.sessionId === id ? null : current))
    setSessions((current) => {
      const index = current.findIndex((s) => s.id === id)
      const rest = current.filter((s) => s.id !== id)
      setActiveId((active) => {
        if (active !== id) return active
        const neighbour = rest[Math.min(index, rest.length - 1)]
        return neighbour ? neighbour.id : null
      })
      return rest
    })
  }, [])

  const reportStatus = useCallback(
    (id: string, info: StatusInfo) => {
      setSessions((current) =>
        current.map((s) =>
          s.id === id
            ? {
                ...s,
                status: info.state,
                serverId: info.serverId ?? (info.state === 'connecting' ? null : s.serverId),
                via: info.via ?? (info.state === 'connecting' ? null : s.via),
                jump: info.jump === undefined ? s.jump : info.jump,
                latencyMs: info.latencyMs === undefined ? s.latencyMs : info.latencyMs,
                detail: info.detail ?? null,
                filesOpen: info.state === 'open' ? s.filesOpen : false,
              }
            : s,
        ),
      )
      // After connecting, "last used" is new, and a host key may have been added.
      if (info.state === 'open') void reloadConnections()
      // The browser only shows a rejected handshake as 1006, without the reason. Usually the
      // sign-in has expired; asking the server then shows the sign-in page.
      // The same for a locked vault: after a server restart it is locked, and the UI does not know it yet.
      if (info.state === 'failed' && (info.detail === 'connection_lost' || info.detail === 'vault_locked')) void refresh()
    },
    [reloadConnections, refresh],
  )

  const showPrompt = useCallback((id: string, kind: PromptKind, data: PromptData) => {
    setPrompt({ sessionId: id, kind, data })
    setActiveId(id)
  }, [])

  const answerPrompt = useCallback(
    (answer: PromptAnswer | null) => {
      if (!prompt) return
      const entry = peekTerminal(prompt.sessionId)
      setPrompt(null)
      if (answer === null) {
        closeSession(prompt.sessionId)
        return
      }
      if (!entry) return
      if ('accept' in answer) entry.answer({ type: 'hostkey', accept: answer.accept })
      else if (prompt.kind === 'passphrase') entry.answer({ type: 'passphrase', value: answer.value })
      else entry.answer({ type: 'password', value: answer.value, store: Boolean(answer.store) })
    },
    [prompt, closeSession],
  )

  const toggleFiles = useCallback((id: string) => {
    setSessions((current) => current.map((s) => (s.id === id ? { ...s, filesOpen: !s.filesOpen } : s)))
  }, [])

  const value = useMemo<WorkspaceValue>(
    () => ({
      connections,
      connectionsError,
      reloadConnections,
      reach,
      sessions,
      activeId,
      activate: setActiveId,
      openConnection,
      openQuick,
      closeSession,
      toggleFiles,
      reportStatus,
      showPrompt,
      prompt,
      answerPrompt,
      listCollapsed,
      setListCollapsed,
    }),
    [connections, connectionsError, reloadConnections, reach, sessions, activeId, openConnection, openQuick, closeSession, toggleFiles, reportStatus, showPrompt, prompt, answerPrompt, listCollapsed, setListCollapsed],
  )

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace outside WorkspaceProvider')
  return value
}
