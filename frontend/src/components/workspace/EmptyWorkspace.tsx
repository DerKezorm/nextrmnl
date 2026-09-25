import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { formatRelative } from '../../lib/format'
import { useWorkspace } from '../../state/workspace'
import { buttonClasses } from '../buttonClasses'
import { Symbol } from '../Symbol'

/** Parses `user@host:port` like the quick connect in PuTTY. */
function parseQuick(input: string): { user: string; host: string; port: number } | null {
  const match = /^(?:([^@\s]+)@)?([^:\s@/]+)(?::(\d{1,5}))?$/.exec(input.trim())
  if (!match) return null
  const port = match[3] ? Number(match[3]) : 22
  if (port < 1 || port > 65535) return null
  return { user: match[1] ?? 'root', host: match[2], port }
}

/** What is shown on the right as long as no session is open. */
export function EmptyWorkspace({ onNew, onOpenList }: { onNew: () => void; onOpenList?: () => void }) {
  const { t } = useTranslation()
  const { connections, openConnection, openQuick } = useWorkspace()
  const [quick, setQuick] = useState('')
  const [error, setError] = useState(false)
  const recent = [...connections]
    .filter((c) => c.last_used_at)
    .sort((a, b) => String(b.last_used_at).localeCompare(String(a.last_used_at)))
    .slice(0, 4)

  function quickConnect(event: FormEvent) {
    event.preventDefault()
    const parsed = parseQuick(quick)
    if (!parsed) {
      setError(true)
      return
    }
    setQuick('')
    setError(false)
    openQuick(parsed)
  }

  return (
    <div className="nt-scroll flex min-h-0 flex-1 items-start justify-center overflow-y-auto px-6 py-12">
      <div className="w-full max-w-2xl">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-500/12 text-accent-400">
            <Symbol name="terminal" className="h-7 w-7" />
          </span>
          <h1 className="text-2xl font-bold tracking-tight">{t('empty.title')}</h1>
          <p className="mt-1.5 text-mist-500">{t('empty.lead')}</p>
        </div>

        <form onSubmit={quickConnect} className="mb-8">
          <label htmlFor="quick" className="mb-1.5 block text-sm font-medium text-mist-300">
            {t('empty.quickLabel')}
          </label>
          <div className="flex gap-2">
            <input
              id="quick"
              value={quick}
              onChange={(event) => {
                setQuick(event.target.value)
                setError(false)
              }}
              placeholder="admin@192.0.2.40:22"
              aria-invalid={error || undefined}
              aria-describedby="quick-hint"
              spellCheck={false}
              autoComplete="off"
              className="min-w-0 flex-1 rounded-full border border-ink-700 bg-ink-900 px-4 py-2.5 font-mono text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none aria-invalid:border-bad-500"
            />
            <button type="submit" className={buttonClasses('primary')}>
              {t('empty.quickConnect')}
            </button>
          </div>
          <p id="quick-hint" className={'mt-1.5 text-xs ' + (error ? 'text-bad-500' : 'text-mist-500')}>
            {error ? t('empty.quickError') : t('empty.quickHint')}
          </p>
        </form>

        {recent.length > 0 && (
          <>
            <h2 className="mb-3 text-sm font-semibold tracking-wide text-mist-500 uppercase">{t('empty.recent')}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {recent.map((connection) => (
                <button
                  key={connection.id}
                  type="button"
                  onClick={() => openConnection(connection.id)}
                  className="flex items-center gap-3 rounded-2xl border border-ink-700 bg-ink-850/80 p-4 text-left transition-colors hover:border-accent-500/50 hover:bg-ink-800"
                >
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-ink-800 text-mist-400">
                    <Symbol name="server" className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-mist-100">{connection.name}</span>
                    <span className="block truncate font-mono text-xs text-mist-500">
                      {connection.user}@{connection.host}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-mist-600">{connection.last_used_at && formatRelative(connection.last_used_at)}</span>
                </button>
              ))}
            </div>
          </>
        )}

        <div className="mt-8 flex flex-wrap justify-center gap-2">
          {onOpenList && (
            <button type="button" onClick={onOpenList} className={buttonClasses('ghost')}>
              <Symbol name="list" />
              {t('empty.allConnections')}
            </button>
          )}
          <button type="button" onClick={onNew} className={buttonClasses('ghost')}>
            <Symbol name="plus" />
            {t('list.new')}
          </button>
        </div>
      </div>
    </div>
  )
}
