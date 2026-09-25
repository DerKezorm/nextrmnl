import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, downloadFile, errorMessage } from '../../api/client'
import type { LogEntry, LogMode, LogModeState } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, EmptyState, Section } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { useLoad } from '../../lib/useLoad'

/** Order like on the server: from sparse to chatty. */
const MODES: LogMode[] = ['quiet', 'normal', 'detailed', 'trace']
/** These two switch themselves off again after the chosen time. */
const DEEP: readonly LogMode[] = ['detailed', 'trace']
const DURATIONS = [30, 120, 480, 0] as const

type Filter = 'ALL' | 'INFO' | 'WARNING' | 'ERROR'
const FILTERS: Filter[] = ['ALL', 'INFO', 'WARNING', 'ERROR']
const COLOR: Record<LogEntry['level'], string> = { DEBUG: 'text-mist-600', INFO: 'text-mist-500', WARNING: 'text-warn-500', ERROR: 'text-bad-500', CRITICAL: 'text-bad-500' }
const NEVER = ['terminal', 'secrets', 'files'] as const

/** The log, only for the operator. Messages in English, a request id per request, four levels. */
export function LogTab() {
  const { t } = useTranslation()
  const notify = useNotice()
  const [asked, setAsked] = useState<LogMode | null>(null)
  const [filter, setFilter] = useState<Filter>('ALL')
  const [search, setSearch] = useState('')
  const [clearing, setClearing] = useState(false)
  const [busy, setBusy] = useState(false)
  const modeState = useLoad(() => api.get<LogModeState>('/api/logs/level'))
  const lines = useLoad(() => {
    const params = new URLSearchParams({ limit: '300' })
    if (filter !== 'ALL') params.set('level', filter)
    if (search.trim()) params.set('search', search.trim())
    return api.get<LogEntry[]>(`/api/logs?${params.toString()}`)
  }, [filter, search])

  const { reload: reloadLines } = lines
  const { reload: reloadMode } = modeState

  useEffect(() => {
    const timer = window.setInterval(() => {
      void reloadLines()
      void reloadMode()
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [reloadLines, reloadMode])

  const mode = modeState.data
  const fixed = mode?.fixed_by_env ?? false

  async function setMode(target: LogMode, minutes: number) {
    setBusy(true)
    try {
      modeState.set(await api.put<LogModeState>('/api/logs/level', { mode: target, minutes }))
      setAsked(null)
      void lines.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  function choose(target: LogMode) {
    if (fixed) return
    if (DEEP.includes(target)) {
      // Even for the level that is already running: this way the time limit can be extended.
      setAsked(asked === target ? null : target)
      return
    }
    setAsked(null)
    if (target !== mode?.mode) void setMode(target, 0)
  }

  async function clear() {
    setBusy(true)
    try {
      await api.delete('/api/logs')
      setClearing(false)
      void lines.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const entries = lines.data ?? []

  return (
    <div className="flex flex-col gap-6">
      <Section title={t('logs.title')} intro={t('logs.intro')}>
        <h3 className="text-sm font-semibold text-mist-100">{t('logs.modeTitle')}</h3>
        <p className="-mt-2 text-xs text-mist-500">{t('logs.modeIntro')}</p>
        {modeState.error && <Banner tone="bad">{modeState.error}</Banner>}
        <div className="flex flex-wrap gap-2">
          {MODES.map((value) => (
            <button
              key={value}
              type="button"
              disabled={fixed || busy}
              onClick={() => choose(value)}
              aria-pressed={mode?.mode === value}
              title={t(`logs.modeDesc.${value}`)}
              className={
                'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ' +
                (mode?.mode === value ? 'border-accent-500/60 bg-accent-500/15 text-accent-400' : asked === value ? 'border-accent-500/40 bg-ink-800 text-mist-100' : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
              }
            >
              {t(`logs.mode.${value}`)}
              {DEEP.includes(value) && <span className="ml-1.5 text-xs font-normal opacity-60">{t('logs.modeTemporary')}</span>}
            </button>
          ))}
        </div>
        {asked && (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-accent-500/30 bg-ink-900/60 px-3 py-2">
            <span className="text-xs text-mist-300">{t('logs.durationQuestion', { mode: t(`logs.mode.${asked}`) })}</span>
            {DURATIONS.map((minutes) => (
              <button
                key={minutes}
                type="button"
                disabled={busy}
                onClick={() => void setMode(asked, minutes)}
                className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-xs text-mist-100 transition-colors hover:border-accent-500/60 hover:text-accent-400 disabled:opacity-50"
              >
                {t(`logs.duration.${minutes}`)}
              </button>
            ))}
            <button type="button" onClick={() => setAsked(null)} className="text-xs text-mist-600 underline hover:text-mist-300">
              {t('common.cancel')}
            </button>
          </div>
        )}
        <p className="text-xs text-mist-500">
          {fixed ? t('logs.modeEnv') : mode?.until ? t('logs.modeUntil', { time: formatDateTime(mode.until) }) : t(`logs.modeDesc.${mode?.mode ?? 'normal'}`) + ' ' + t('logs.modeNoLimit')}
        </p>
        {mode?.mode === 'trace' && !fixed && <p className="text-xs text-warn-500">{t('logs.traceWarning')}</p>}
      </Section>

      {/* Deliberately stands here as its own box: nextrmnl holds the access to every machine, the log must not give any of that away. */}
      <Section title={t('logs.neverTitle')} intro={t('logs.neverLead')}>
        <ul className="flex flex-col gap-2">
          {NEVER.map((item) => (
            <li key={item} className="flex items-start gap-2.5 text-sm text-mist-200">
              <Symbol name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
              {t(`logs.never.${item}`)}
            </li>
          ))}
        </ul>
        <p className="text-xs leading-relaxed text-mist-500">{t('logs.alwaysLead')}</p>
      </Section>

      <Section title={t('logs.messagesTitle')}>
        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              aria-pressed={filter === value}
              className={
                'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                (filter === value ? 'border-accent-500/60 bg-accent-500/15 text-accent-400' : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
              }
            >
              {value === 'ALL' ? t('logs.levelAll') : value}
            </button>
          ))}
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('logs.searchPlaceholder')}
            aria-label={t('logs.searchPlaceholder')}
            className="min-w-40 flex-1 rounded-full border border-ink-700 bg-ink-900 px-4 py-1.5 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
          <Button variant="ghost" size="sm" onClick={() => void downloadFile('/api/logs/download', 'nextrmnl-log.txt').catch((error) => notify(errorMessage(error)))}>
            <Symbol name="download" className="h-3.5 w-3.5" />
            {t('logs.download')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setClearing(true)}>
            {t('logs.clear')}
          </Button>
        </div>
        <p className="-mt-2 text-xs text-mist-600">{t('logs.levelHint')}</p>
        {lines.error && <Banner tone="bad">{lines.error}</Banner>}

        {entries.length === 0 ? (
          <EmptyState title={t('logs.empty')} />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-ink-700 bg-ink-900/60">
            <table className="w-full min-w-[46rem] text-left text-xs">
              <thead className="border-b border-ink-700 text-mist-600">
                <tr>
                  <th className="px-3 py-2 font-medium">{t('logs.time')}</th>
                  <th className="px-3 py-2 font-medium">{t('logs.level')}</th>
                  <th className="px-3 py-2 font-medium">{t('logs.source')}</th>
                  <th className="px-3 py-2 font-medium">{t('logs.requestId')}</th>
                  <th className="px-3 py-2 font-medium">{t('logs.message')}</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((line, index) => (
                  <tr key={`${line.time}-${index}`} className="border-b border-ink-700/50 last:border-b-0">
                    <td className="px-3 py-2 whitespace-nowrap text-mist-600 tabular-nums">{line.time}</td>
                    <td className={'px-3 py-2 font-semibold ' + COLOR[line.level]}>{line.level}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-mist-600">{line.logger}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {line.request_id ? (
                        // A click filters down to exactly this request: the path from the id in the error message to the flow.
                        <button type="button" onClick={() => setSearch(line.request_id ?? '')} title={t('logs.requestIdHint')} className="font-mono text-accent-400 hover:underline">
                          {line.request_id}
                        </button>
                      ) : (
                        <span className="text-mist-600">–</span>
                      )}
                      {line.user && <span className="ml-2 text-mist-600">{line.user}</span>}
                    </td>
                    <td className="px-3 py-2 text-mist-300">{line.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-mist-600">{t('logs.retention')}</p>
      </Section>

      <Dialog
        open={clearing}
        title={t('logs.clear')}
        onClose={() => setClearing(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setClearing(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void clear()}>
              {t('logs.clearConfirm')}
            </Button>
          </>
        }
      >
        <Banner tone="warn">{t('logs.confirmClear')}</Banner>
      </Dialog>
    </div>
  )
}
