import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { api, errorMessage } from '../api/client'
import type { RunningSession, SessionEnd, SessionRecord } from '../api/types'
import { useAuth } from '../auth'
import { useNotice } from '../components/Notice'
import { Symbol } from '../components/Symbol'
import { TabRow } from '../components/TabRow'
import { Badge, Banner, Button, Card, PageHeader } from '../components/ui'
import { formatDateTime, formatDuration } from '../lib/format'
import { useLoad } from '../lib/useLoad'
import { useWorkspace } from '../state/workspace'

function Avatar({ name }: { name: string }) {
  return <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink-800 text-xs font-semibold text-mist-300 uppercase">{name.slice(0, 1)}</span>
}

function EndBadge({ end }: { end: SessionEnd }) {
  const { t } = useTranslation()
  const tone = { running: 'ok', normal: 'neutral', failed: 'bad', hostkey: 'warn', cut: 'warn' } as const
  return <Badge tone={tone[end]}>{t(`sessions.end.${end}`)}</Badge>
}

function duration(record: SessionRecord): string {
  const start = new Date(record.started_at).getTime()
  const end = record.ended_at ? new Date(record.ended_at).getTime() : Date.now()
  return formatDuration((end - start) / 1000)
}

function elapsedSince(iso: string): string {
  return formatDuration((Date.now() - new Date(iso).getTime()) / 1000)
}

/** Who is currently connected and who was. The operator sees all accounts, a member only themselves. */
export function SessionsPage() {
  const { t } = useTranslation()
  const notify = useNotice()
  const navigate = useNavigate()
  const { account } = useAuth()
  const { sessions, activate, closeSession } = useWorkspace()
  const [filter, setFilter] = useState<string>('all')
  const running = useLoad(() => api.get<RunningSession[]>('/api/sessions/running'))
  const history = useLoad(() => api.get<SessionRecord[]>('/api/sessions/history?limit=300'))
  const { reload: reloadRunning } = running

  useEffect(() => {
    const timer = window.setInterval(() => void reloadRunning(), 10_000)
    return () => window.clearInterval(timer)
  }, [reloadRunning])

  const ownIds = new Map(sessions.filter((s) => s.serverId).map((s) => [s.serverId as string, s]))
  const records = history.data ?? []
  const accounts = [...new Set(records.map((r) => r.account))].sort()
  const shown = records.filter((r) => r.end !== 'running' && (filter === 'all' || r.account === filter))

  async function disconnect(id: string) {
    try {
      await api.post(`/api/sessions/${encodeURIComponent(id)}/disconnect`)
      await running.reload()
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t('sessions.title')} lead={t('sessions.lead')} />

      <Card className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">{t('sessions.running')}</h2>
          <Badge tone="ok">{running.data?.length ?? 0}</Badge>
        </div>
        {running.error && <Banner tone="bad">{running.error}</Banner>}
        <ul className="flex flex-col divide-y divide-ink-700">
          {(running.data ?? []).map((record) => {
            const own = ownIds.get(record.id)
            return (
              <li key={record.id} className="flex flex-wrap items-center gap-3 py-3">
                <Avatar name={record.account} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-mist-100">
                    {record.account} <span className="text-mist-500">→</span> {record.name || record.target}
                  </p>
                  <p className="font-mono text-xs text-mist-500">
                    {record.target} · {t('sessions.since', { when: formatDateTime(record.started_at), duration: elapsedSince(record.started_at) })} ·{' '}
                    {own ? t('sessions.thisBrowser') : record.from_ip}
                  </p>
                </div>
                {own && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      activate(own.id)
                      navigate('/')
                    }}
                  >
                    <Symbol name="terminal" className="h-3.5 w-3.5" />
                    {t('sessions.show')}
                  </Button>
                )}
                {(own || account?.role === 'operator') && (
                  <Button variant="danger" size="sm" onClick={() => (own ? closeSession(own.id) : void disconnect(record.id))}>
                    {t('sessions.disconnect')}
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
        {running.data?.length === 0 && <p className="text-sm text-mist-500">{t('sessions.noneRunning')}</p>}
      </Card>

      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{t('sessions.history')}</h2>
          {accounts.length > 1 && (
            <TabRow small label={t('sessions.filter')} active={filter} onChange={setFilter} tabs={[{ value: 'all', label: t('sessions.all') }, ...accounts.map((name) => ({ value: name, label: name }))]} />
          )}
        </div>
        {history.error && <Banner tone="bad">{history.error}</Banner>}
        {shown.length === 0 ? (
          <p className="text-sm text-mist-500">{t('sessions.noHistory')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="text-xs tracking-wide text-mist-500 uppercase">
                <tr>
                  <th className="py-2 pr-4 font-semibold">{t('sessions.colAccount')}</th>
                  <th className="py-2 pr-4 font-semibold">{t('sessions.colConnection')}</th>
                  <th className="py-2 pr-4 font-semibold">{t('sessions.colStart')}</th>
                  <th className="py-2 pr-4 font-semibold">{t('sessions.colDuration')}</th>
                  <th className="py-2 pr-4 font-semibold">{t('sessions.colFrom')}</th>
                  <th className="py-2 font-semibold">{t('sessions.colEnd')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-700">
                {shown.map((record) => (
                  <tr key={record.id}>
                    <td className="py-2.5 pr-4 text-mist-200">{record.account}</td>
                    <td className="py-2.5 pr-4 text-mist-200" title={record.target}>
                      {record.name || record.target}
                    </td>
                    <td className="py-2.5 pr-4 text-mist-400">{formatDateTime(record.started_at)}</td>
                    <td className="py-2.5 pr-4 text-mist-400 tabular-nums">{duration(record)}</td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-mist-500">{record.from_ip}</td>
                    <td className="py-2.5" title={record.detail}>
                      <EndBadge end={record.end} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-mist-500">{t('sessions.keepNote')}</p>
      </Card>
    </div>
  )
}
