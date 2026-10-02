/**
 * The split view: which session shows in which field. Pure functions, the workspace keeps the state.
 *
 * A field holds a session id or nothing. Sessions keep their tabs; a field only shows one of them.
 */

export type Layout = 'single' | 'columns' | 'rows' | 'grid'
export const LAYOUTS: readonly Layout[] = ['single', 'columns', 'rows', 'grid']
export type Panes = (string | null)[]

export const PANE_COUNT: Record<Layout, number> = { single: 1, columns: 2, rows: 2, grid: 4 }

export function isLayout(value: unknown): value is Layout {
  return typeof value === 'string' && (LAYOUTS as readonly string[]).includes(value)
}

/**
 * The fields for a layout: what already shows and still exists stays where it is, the active session is placed if
 * it is missing (into the field it replaces, `focus`, when nothing is free), and free fields take the remaining
 * sessions in tab order.
 */
export function arrange(layout: Layout, current: Panes, sessionIds: string[], activeId: string | null, focus = 0): Panes {
  const count = PANE_COUNT[layout]
  const alive = new Set(sessionIds)
  const panes: Panes = Array.from({ length: count }, (_, index) => {
    const id = current[index] ?? null
    return id !== null && alive.has(id) ? id : null
  })
  // The same session twice: the later field lets go.
  panes.forEach((id, index) => {
    if (id !== null && panes.indexOf(id) !== index) panes[index] = null
  })
  if (activeId !== null && alive.has(activeId) && !panes.includes(activeId)) {
    const free = panes.indexOf(null)
    panes[free >= 0 ? free : Math.min(Math.max(focus, 0), count - 1)] = activeId
  }
  for (const id of sessionIds) {
    if (panes.includes(id)) continue
    const free = panes.indexOf(null)
    if (free < 0) break
    panes[free] = id
  }
  return panes
}

/** Puts a session into a field; if it showed in another one, the two fields swap. */
export function place(panes: Panes, index: number, id: string | null): Panes {
  const next = [...panes]
  const before = next.indexOf(id)
  if (id !== null && before >= 0 && before !== index) next[before] = next[index] ?? null
  next[index] = id
  return next
}
