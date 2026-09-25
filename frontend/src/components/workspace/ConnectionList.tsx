import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { Connection, Reach } from '../../api/types'
import { useWorkspace } from '../../state/workspace'
import { Dialog } from '../Dialog'
import { useNotice } from '../Notice'
import { Symbol } from '../Symbol'
import { Banner, Button } from '../ui'

function ReachDot({ reach }: { reach: Reach }) {
  const { t } = useTranslation()
  const tone = reach === 'up' ? 'bg-ok-500' : reach === 'down' ? 'bg-bad-500' : 'bg-mist-600'
  const label = t(`reach.${reach}`)
  return <span className={'h-2 w-2 shrink-0 rounded-full ' + tone} title={label} aria-label={label} role="img" />
}

const MENU_WIDTH = 208
const MENU_HEIGHT = 168

/**
 * The menu for a row. It is attached to `body`, not inside the list: the list clips anything that
 * extends past its edge, and in the floating version with two connections only the first entry was visible.
 */
function RowMenu({ connection, anchor, onEdit, onRemove, onClose }: { connection: Connection; anchor: DOMRect; onEdit: (share?: boolean) => void; onRemove: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const { openConnection } = useWorkspace()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onPointer(event: PointerEvent) {
      const target = event.target as HTMLElement
      // The button that opens the menu closes it itself. Otherwise it would close and immediately reopen.
      if (ref.current?.contains(target) || target.closest('[data-menu-toggle]')) return
      onClose()
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    // When scrolling or resizing, the position no longer matches; better to close it then.
    window.addEventListener('scroll', onClose, true)
    window.addEventListener('resize', onClose)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onClose, true)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  const item = 'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm hover:bg-ink-800'
  const own = !connection.shared_by
  const upwards = anchor.bottom + MENU_HEIGHT > window.innerHeight
  const style = {
    top: upwards ? Math.max(8, anchor.top - 4 - MENU_HEIGHT) : anchor.bottom + 4,
    left: Math.max(8, Math.min(anchor.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8)),
    width: MENU_WIDTH,
  }
  return createPortal(
    <div ref={ref} role="menu" style={style} className="fixed z-50 rounded-xl border border-ink-700 bg-ink-850 p-1 shadow-2xl shadow-black/50">
      <button type="button" role="menuitem" className={item} onClick={() => { openConnection(connection.id); onClose() }}>
        <Symbol name="terminal" className="h-4 w-4 text-mist-500" />
        {t('list.connect')}
      </button>
      <button type="button" role="menuitem" className={item} onClick={() => { onEdit(); onClose() }}>
        <Symbol name={own ? 'pencil' : 'info'} className="h-4 w-4 text-mist-500" />
        {own ? t('list.edit') : t('list.details')}
      </button>
      {own && (
        <button type="button" role="menuitem" className={item} onClick={() => { onEdit(true); onClose() }}>
          <Symbol name="share" className="h-4 w-4 text-mist-500" />
          {t('list.share')}
        </button>
      )}
      {own && (
        <button type="button" role="menuitem" className={item + ' text-bad-500'} onClick={() => { onRemove(); onClose() }}>
          <Symbol name="trash" className="h-4 w-4" />
          {t('list.remove')}
        </button>
      )}
    </div>,
    document.body,
  )
}

/**
 * The connections by group. As a card next to the terminal, or collapsed as a
 * floating card over the terminal that closes again after a selection.
 */
export function ConnectionList({
  onEdit,
  onNew,
  onPicked,
  floating = false,
  onClose,
}: {
  onEdit: (connection: Connection, share?: boolean) => void
  onNew: () => void
  onPicked?: () => void
  floating?: boolean
  onClose?: () => void
}) {
  const { t } = useTranslation()
  const notify = useNotice()
  const { connections, connectionsError, reloadConnections, reach, sessions, activeId, openConnection, setListCollapsed } = useWorkspace()
  const [query, setQuery] = useState('')
  const [closedGroups, setClosedGroups] = useState<Set<string>>(new Set())
  const [menuFor, setMenuFor] = useState<{ id: number; anchor: DOMRect } | null>(null)
  const [removing, setRemoving] = useState<Connection | null>(null)
  const [busy, setBusy] = useState(false)

  const activeConnection = sessions.find((s) => s.id === activeId)?.connectionId
  const openConnections = new Set(sessions.filter((s) => s.status === 'open' || s.status === 'connecting').map((s) => s.connectionId))

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = connections.filter(
      (c) => !needle || c.name.toLowerCase().includes(needle) || c.host.toLowerCase().includes(needle) || c.user.toLowerCase().includes(needle),
    )
    const byGroup = new Map<string, Connection[]>()
    for (const connection of matching) {
      const group = connection.shared_by ? t('list.sharedWithMe') : connection.group || t('list.noGroup')
      byGroup.set(group, [...(byGroup.get(group) ?? []), connection])
    }
    return [...byGroup.entries()]
  }, [connections, query, t])

  async function remove() {
    if (!removing) return
    setBusy(true)
    try {
      await api.delete(`/api/connections/${removing.id}`)
      setRemoving(null)
      await reloadConnections()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const card = floating
    ? 'pointer-events-auto flex max-h-full w-80 max-w-[calc(100vw-3rem)] flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/95 shadow-2xl shadow-black/60 backdrop-blur-xl'
    : 'flex h-full w-72 shrink-0 flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/80 shadow-2xl shadow-black/30 backdrop-blur'

  return (
    <aside className={card} aria-label={t('list.title')}>
      <div className="flex items-center gap-2 border-b border-ink-700 p-2.5">
        <div className="relative min-w-0 flex-1">
          <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-mist-600" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('list.search')}
            aria-label={t('list.search')}
            className="w-full rounded-full border border-ink-700 bg-ink-850 py-1.5 pr-3 pl-9 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
        </div>
        <button type="button" onClick={onNew} className="rounded-full bg-accent-500 p-1.5 text-on-accent hover:bg-accent-400" title={t('list.new')} aria-label={t('list.new')}>
          <Symbol name="plus" />
        </button>
        {floating ? (
          <>
            <button
              type="button"
              onClick={() => {
                setListCollapsed(false)
                onClose?.()
              }}
              className="hidden rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100 md:block"
              title={t('list.dock')}
              aria-label={t('list.dock')}
            >
              <Symbol name="pin" />
            </button>
            <button type="button" onClick={onClose} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100" title={t('common.close')} aria-label={t('common.close')}>
              <Symbol name="close" />
            </button>
          </>
        ) : (
          <button type="button" onClick={() => setListCollapsed(true)} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100" title={t('list.collapse')} aria-label={t('list.collapse')}>
            <Symbol name="chevronLeft" />
          </button>
        )}
      </div>

      <div className="nt-scroll min-h-0 flex-1 overflow-y-auto p-2">
        {connectionsError && <div className="p-1"><Banner tone="bad">{connectionsError}</Banner></div>}
        {groups.length === 0 && !connectionsError && <p className="px-3 py-6 text-center text-sm text-mist-500">{connections.length === 0 ? t('list.empty') : t('list.nothingFound')}</p>}
        {groups.map(([group, items]) => {
          const closed = closedGroups.has(group)
          return (
            <section key={group} className="mb-2">
              <button
                type="button"
                aria-expanded={!closed}
                onClick={() =>
                  setClosedGroups((current) => {
                    const next = new Set(current)
                    if (next.has(group)) next.delete(group)
                    else next.add(group)
                    return next
                  })
                }
                className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold tracking-wide text-mist-500 uppercase hover:text-mist-200"
              >
                <Symbol name={closed ? 'chevronRight' : 'chevronDown'} className="h-3.5 w-3.5" />
                {group}
                <span className="ml-auto font-normal tabular-nums">{items.length}</span>
              </button>
              {!closed && (
                <ul className="flex flex-col gap-0.5">
                  {items.map((connection) => {
                    const active = activeConnection === connection.id
                    const running = openConnections.has(connection.id)
                    return (
                      <li key={connection.id} className="group relative">
                        <button
                          type="button"
                          onClick={() => {
                            openConnection(connection.id)
                            onPicked?.()
                          }}
                          className={
                            'flex w-full items-center gap-2.5 rounded-xl py-2 pr-9 pl-3 text-left transition-colors ' +
                            (active ? 'bg-accent-500/12 text-mist-100' : 'text-mist-300 hover:bg-ink-800 hover:text-mist-100')
                          }
                        >
                          <ReachDot reach={reach[connection.id]?.reach ?? 'unknown'} />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className={'truncate text-sm font-medium ' + (active ? 'text-accent-400' : '')}>{connection.name}</span>
                              {connection.jump_id !== null && <Symbol name="jump" className="h-3.5 w-3.5 shrink-0 text-mist-600" />}
                              {(connection.shared_with.length > 0 || connection.shared_by) && <Symbol name="share" className="h-3.5 w-3.5 shrink-0 text-mist-600" />}
                            </span>
                            <span className="block truncate font-mono text-[11px] text-mist-600">
                              {connection.user}@{connection.host}
                              {connection.port !== 22 ? `:${connection.port}` : ''}
                            </span>
                          </span>
                          {running && <span className="rounded-full bg-accent-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent-400">{t('list.open')}</span>}
                        </button>
                        <button
                          type="button"
                          onClick={(event) =>
                            setMenuFor(menuFor?.id === connection.id ? null : { id: connection.id, anchor: event.currentTarget.getBoundingClientRect() })
                          }
                          aria-label={t('list.more', { name: connection.name })}
                          aria-haspopup="menu"
                          data-menu-toggle
                          aria-expanded={menuFor?.id === connection.id}
                          className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded-full p-1 text-mist-500 opacity-0 group-hover:opacity-100 hover:bg-ink-700 hover:text-mist-100 focus:opacity-100 aria-expanded:opacity-100"
                        >
                          <Symbol name="more" />
                        </button>
                        {menuFor?.id === connection.id && (
                          <RowMenu
                            connection={connection}
                            anchor={menuFor.anchor}
                            onEdit={(share) => onEdit(connection, share)}
                            onRemove={() => setRemoving(connection)}
                            onClose={() => setMenuFor(null)}
                          />
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          )
        })}
      </div>

      <div className="border-t border-ink-700 px-3 py-2 text-[11px] text-mist-600">{t('list.footer', { count: connections.length })}</div>

      <Dialog
        open={removing !== null}
        title={t('list.removeTitle', { name: removing?.name ?? '' })}
        onClose={() => setRemoving(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void remove()}>
              {t('list.remove')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('list.removeText')}</p>
      </Dialog>
    </aside>
  )
}
