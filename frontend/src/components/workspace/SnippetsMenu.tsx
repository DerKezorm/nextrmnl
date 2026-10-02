import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { Snippet } from '../../api/types'
import { insertText } from '../../lib/terminalCache'
import { useWorkspace } from '../../state/workspace'
import { Popover } from '../Popover'
import { Symbol } from '../Symbol'
import { Banner, Button } from '../ui'

/** Recently used first, then by name. */
function ordered(snippets: Snippet[]): Snippet[] {
  return [...snippets].sort((a, b) => {
    if (a.last_used_at && b.last_used_at) return b.last_used_at.localeCompare(a.last_used_at)
    if (a.last_used_at) return -1
    if (b.last_used_at) return 1
    return a.name.localeCompare(b.name)
  })
}

type Draft = { id: number | null; name: string; command: string }

/**
 * Commands kept at hand. A click types the command into the active terminal without running it; Enter is the
 * person's. With "type into all" on, it goes to every field of the split view.
 */
export function SnippetsMenu() {
  const { t } = useTranslation()
  const { sessions, activeId, broadcast } = useWorkspace()
  const active = sessions.find((s) => s.id === activeId)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const [snippets, setSnippets] = useState<Snippet[] | null>(null)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  const load = async () => {
    try {
      setSnippets(await api.get<Snippet[]>('/api/snippets'))
      setError(null)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  useEffect(() => {
    if (!anchor) return
    void load()
    setQuery('')
    setDraft(null)
    setConfirmDelete(null)
    window.setTimeout(() => searchRef.current?.focus(), 0)
  }, [anchor])

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const all = ordered(snippets ?? [])
    return needle ? all.filter((s) => s.name.toLowerCase().includes(needle) || s.command.toLowerCase().includes(needle)) : all
  }, [snippets, query])

  const canInsert = active?.status === 'open'

  const use = (snippet: Snippet) => {
    if (!active || !canInsert) return
    insertText(active.id, snippet.command)
    setAnchor(null)
    void api.post(`/api/snippets/${snippet.id}/used`).catch(() => undefined)
  }

  const save = async () => {
    if (!draft) return
    setBusy(true)
    try {
      const body = { name: draft.name, command: draft.command }
      if (draft.id === null) await api.post('/api/snippets', body)
      else await api.put(`/api/snippets/${draft.id}`, body)
      setDraft(null)
      await load()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.delete(`/api/snippets/${id}`)
      setConfirmDelete(null)
      await load()
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  return (
    <>
      <button
        type="button"
        data-popover-toggle
        aria-expanded={anchor !== null}
        aria-haspopup="dialog"
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className={
          'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ' +
          (anchor ? 'bg-accent-500/15 text-accent-400' : 'text-mist-400 hover:bg-ink-800 hover:text-mist-100')
        }
        title={t('snippets.title')}
      >
        <Symbol name="command" />
        <span className="hidden xl:inline">{t('snippets.title')}</span>
      </button>
      {anchor && (
        <Popover anchor={anchor} width={380} label={t('snippets.title')} onClose={() => setAnchor(null)}>
          <div className="flex items-center gap-2 border-b border-ink-700 p-2">
            <div className="relative min-w-0 flex-1">
              <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-mist-600" />
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && shown[0]) {
                    event.preventDefault()
                    use(shown[0])
                  }
                }}
                placeholder={t('snippets.search')}
                aria-label={t('snippets.search')}
                className="w-full rounded-full border border-ink-700 bg-ink-850 py-1.5 pr-3 pl-9 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
              />
            </div>
            <button
              type="button"
              onClick={() => setDraft({ id: null, name: '', command: '' })}
              className="rounded-full bg-accent-500 p-1.5 text-on-accent hover:bg-accent-400"
              title={t('snippets.new')}
              aria-label={t('snippets.new')}
            >
              <Symbol name="plus" />
            </button>
          </div>

          {error && (
            <div className="p-2">
              <Banner tone="bad">{error}</Banner>
            </div>
          )}
          {broadcast && <p className="border-b border-ink-700 bg-bad-500/10 px-3 py-1.5 text-xs text-bad-500">{t('snippets.toAll')}</p>}
          {!canInsert && <p className="border-b border-ink-700 px-3 py-1.5 text-xs text-mist-500">{t('snippets.noTerminal')}</p>}

          {draft && (
            <form
              className="flex flex-col gap-2 border-b border-ink-700 p-3"
              onSubmit={(event) => {
                event.preventDefault()
                void save()
              }}
            >
              <label className="text-xs font-medium text-mist-300" htmlFor="snippet-name">
                {t('snippets.name')}
              </label>
              <input
                id="snippet-name"
                autoFocus
                value={draft.name}
                maxLength={64}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                className="rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              />
              <label className="text-xs font-medium text-mist-300" htmlFor="snippet-command">
                {t('snippets.command')}
              </label>
              <textarea
                id="snippet-command"
                value={draft.command}
                rows={3}
                spellCheck={false}
                onChange={(event) => setDraft({ ...draft, command: event.target.value })}
                className="rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 font-mono text-xs text-mist-100 focus:border-accent-500 focus:outline-none"
              />
              <p className="text-[11px] text-mist-500">{t('snippets.commandHint')}</p>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>
                  {t('common.cancel')}
                </Button>
                <Button size="sm" type="submit" loading={busy} disabled={!draft.name.trim() || !draft.command.trim()}>
                  {t('common.save')}
                </Button>
              </div>
            </form>
          )}

          <ul className="nt-scroll min-h-0 flex-1 overflow-y-auto p-1">
            {snippets !== null && shown.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-mist-500">{snippets.length === 0 ? t('snippets.empty') : t('snippets.nothingFound')}</li>
            )}
            {shown.map((snippet) => (
              <li key={snippet.id} className="group relative">
                <button
                  type="button"
                  disabled={!canInsert}
                  onClick={() => use(snippet)}
                  className="flex w-full flex-col items-start gap-0.5 rounded-lg py-2 pr-16 pl-3 text-left hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-60"
                  title={t('snippets.insert')}
                >
                  <span className="text-sm font-medium text-mist-100">{snippet.name}</span>
                  <span className="line-clamp-2 font-mono text-[11px] break-all whitespace-pre-wrap text-mist-500">{snippet.command}</span>
                </button>
                <span className="absolute top-2 right-1.5 flex gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100">
                  {confirmDelete === snippet.id ? (
                    <button type="button" onClick={() => void remove(snippet.id)} className="rounded-full bg-bad-500/15 px-2 py-0.5 text-[11px] font-medium text-bad-500">
                      {t('snippets.reallyDelete')}
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => setDraft({ id: snippet.id, name: snippet.name, command: snippet.command })}
                        className="rounded-full p-1 text-mist-500 hover:bg-ink-700 hover:text-mist-100"
                        title={t('snippets.edit')}
                        aria-label={t('snippets.editNamed', { name: snippet.name })}
                      >
                        <Symbol name="pencil" className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDelete(snippet.id)}
                        className="rounded-full p-1 text-mist-500 hover:bg-bad-500/10 hover:text-bad-500"
                        title={t('snippets.delete')}
                        aria-label={t('snippets.deleteNamed', { name: snippet.name })}
                      >
                        <Symbol name="trash" className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Popover>
      )}
    </>
  )
}
