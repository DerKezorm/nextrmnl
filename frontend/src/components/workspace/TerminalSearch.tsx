import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { peekTerminal } from '../../lib/terminalCache'
import { Symbol } from '../Symbol'

/** Highlights of the matches; the active one in the accent color, the rest quieter. */
const DECORATIONS = {
  matchBackground: '#a78bfa55',
  matchOverviewRuler: '#a78bfa',
  activeMatchBackground: '#f59e0b',
  activeMatchColorOverviewRuler: '#f59e0b',
}

/**
 * Search in the terminal's history: Ctrl+Shift+F or the magnifier in the status line. Enter finds the next match
 * upwards (the newest output is at the bottom), Shift+Enter the one below, Escape closes and goes back to typing.
 */
export function TerminalSearch({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [result, setResult] = useState<{ index: number; count: number } | null>(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
    const entry = peekTerminal(sessionId)
    if (!entry) return
    const subscription = entry.search.onDidChangeResults(({ resultIndex, resultCount }) => setResult({ index: resultIndex, count: resultCount }))
    return () => {
      subscription.dispose()
      entry.search.clearDecorations()
    }
  }, [sessionId])

  const find = (direction: 'up' | 'down', text = query, incremental = false, matchCase = caseSensitive) => {
    const entry = peekTerminal(sessionId)
    if (!entry) return
    if (!text) {
      entry.search.clearDecorations()
      setResult(null)
      return
    }
    const options = { caseSensitive: matchCase, incremental, decorations: DECORATIONS }
    const found = direction === 'up' ? entry.search.findPrevious(text, options) : entry.search.findNext(text, options)
    if (!found) setResult({ index: -1, count: 0 })
  }

  const close = () => {
    peekTerminal(sessionId)?.search.clearDecorations()
    onClose()
    peekTerminal(sessionId)?.term.focus()
  }

  const counter = result === null || !query ? '' : result.count === 0 ? t('search.none') : result.index < 0 ? t('search.many', { count: result.count }) : t('search.position', { index: result.index + 1, count: result.count })

  return (
    <div role="search" className="absolute top-2 right-4 z-10 flex items-center gap-1 rounded-xl border border-ink-700 bg-ink-900/95 p-1 shadow-lg">
      <label className="sr-only" htmlFor={`search-${sessionId}`}>
        {t('search.label')}
      </label>
      <input
        id={`search-${sessionId}`}
        ref={inputRef}
        value={query}
        placeholder={t('search.placeholder')}
        spellCheck={false}
        autoComplete="off"
        className="w-44 rounded-lg bg-ink-800 px-2 py-1 font-mono text-xs text-mist-100 outline-none placeholder:text-mist-500 focus:ring-1 focus:ring-accent-500 sm:w-56"
        onChange={(event) => {
          setQuery(event.target.value)
          find('up', event.target.value, true)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            close()
          } else if (event.key === 'Enter') {
            event.preventDefault()
            find(event.shiftKey ? 'down' : 'up')
          }
        }}
      />
      <span className="min-w-[4.5rem] px-1 text-center font-mono text-[11px] text-mist-500 tabular-nums" aria-live="polite">
        {counter}
      </span>
      <button
        type="button"
        aria-pressed={caseSensitive}
        title={t('search.caseSensitive')}
        aria-label={t('search.caseSensitive')}
        className={'rounded-md px-1.5 py-1 font-mono text-[11px] ' + (caseSensitive ? 'bg-accent-500/20 text-accent-400' : 'text-mist-400 hover:bg-ink-800')}
        onClick={() => {
          setCaseSensitive(!caseSensitive)
          find('up', query, true, !caseSensitive)
        }}
      >
        Aa
      </button>
      <button type="button" title={t('search.previous')} aria-label={t('search.previous')} className="rounded-md p-1 text-mist-300 hover:bg-ink-800" onClick={() => find('up')}>
        <Symbol name="up" className="h-3.5 w-3.5" />
      </button>
      <button type="button" title={t('search.next')} aria-label={t('search.next')} className="rounded-md p-1 text-mist-300 hover:bg-ink-800" onClick={() => find('down')}>
        <Symbol name="chevronDown" className="h-3.5 w-3.5" />
      </button>
      <button type="button" title={t('search.close')} aria-label={t('search.close')} className="rounded-md p-1 text-mist-300 hover:bg-ink-800" onClick={close}>
        <Symbol name="close" className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}
