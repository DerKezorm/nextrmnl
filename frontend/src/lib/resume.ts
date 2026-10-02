/**
 * Bringing shells back after a reload. The server keeps an open shell for a while when its browser leaves; this
 * tab remembers which shells it showed (sessionStorage, per tab) and asks for them again on the next load.
 */

import type { RunningSession } from '../api/types'
import type { Quick } from './terminalCache'

const TABS_KEY = 'nextrmnl.tabs'

export function storedTabs(): string[] {
  try {
    const value = JSON.parse(sessionStorage.getItem(TABS_KEY) ?? '[]') as unknown
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

export function storeTabs(ids: string[]): void {
  try {
    sessionStorage.setItem(TABS_KEY, JSON.stringify(ids))
  } catch {
    // Then a reload brings back only the shells nobody watches.
  }
}

/** `user@host:port` as the server labels a quick connection; IPv6 hosts keep their colons. */
export function quickFromLabel(label: string): Quick | null {
  const match = /^([^@]+)@(.+):(\d+)$/.exec(label)
  return match ? { user: match[1], host: match[2], port: Number(match[3]) } : null
}

/**
 * Which running shells to bring back: the ones this tab showed, in their order, then own ones nobody watches,
 * oldest first. A shell another window shows right now is left alone unless this tab showed it before the reload.
 */
export function shellsToResume(running: RunningSession[], shownHere: string[]): RunningSession[] {
  const mine = running.filter((r) => r.mine && r.state === 'open')
  const here = shownHere.map((id) => mine.find((r) => r.id === id)).filter((r): r is RunningSession => Boolean(r))
  const waiting = mine.filter((r) => r.detached && !shownHere.includes(r.id)).sort((a, b) => a.started_at.localeCompare(b.started_at))
  return [...here, ...waiting]
}
