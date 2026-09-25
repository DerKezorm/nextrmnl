import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { api, downloadFile, errorMessage } from '../../api/client'
import type { Backup, BackupBrief, BackupList, BackupSchedule } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { FileField } from '../../components/FileField'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Badge, Banner, Button, Card, Field, PageLoading, Section, SelectField } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { MIN_PASSWORD } from '../../lib/rules'
import { useLoad } from '../../lib/useLoad'

const SCHEDULES: BackupSchedule[] = ['off', 'daily', 'weekly', 'monthly']
const MIB = 1048576

function formatSize(bytes: number): string {
  if (bytes >= MIB) return `${(bytes / MIB).toFixed(1)} MiB`
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`
}

/**
 * Backups of the server: create, view, download, restore. Looks like Nexview and nexcrate.
 *
 * The button that matters is "Download": the copies that nextrmnl creates on its own live in the
 * same directory as the database. It only becomes a backup once it leaves the machine.
 */
export function BackupTab() {
  const { t } = useTranslation()
  const notify = useNotice()
  const list = useLoad(() => api.get<BackupList>('/api/backups'))
  const [creating, setCreating] = useState(false)
  const [note, setNote] = useState('')
  const [download, setDownload] = useState<Backup | null>(null)
  const [remove, setRemove] = useState<Backup | null>(null)
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const passwordOk = password.length >= MIN_PASSWORD && password === repeat

  async function saveSettings(values: Record<string, unknown>) {
    try {
      await api.put('/api/settings', values)
      await list.reload()
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  async function create() {
    setBusy(true)
    try {
      await api.post('/api/backups', { note: note.trim() })
      setCreating(false)
      setNote('')
      await list.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function fetchArchive() {
    if (!download) return
    setBusy(true)
    try {
      await downloadFile(`/api/backups/${encodeURIComponent(download.name)}/archive`, download.name.replace(/\.db$/, '.zip'), { password })
      setDownload(null)
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function deleteBackup() {
    if (!remove) return
    setBusy(true)
    try {
      await api.delete(`/api/backups/${encodeURIComponent(remove.name)}`)
      setRemove(null)
      await list.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const data = list.data
  if (!data) return list.error ? <Banner tone="bad">{list.error}</Banner> : <PageLoading />

  return (
    <div className="flex flex-col gap-6">
      <Section title={t('backups.title')} intro={t('backups.intro')}>
        <h3 className="text-sm font-semibold text-mist-100">{t('backups.autoTitle')}</h3>
        <div className="grid gap-4 sm:grid-cols-[14rem_8rem_1fr] sm:items-end">
          <SelectField label={t('backups.scheduleLabel')} value={data.schedule} onChange={(value) => void saveSettings({ backup_schedule: value })}>
            {SCHEDULES.map((value) => (
              <option key={value} value={value}>
                {t(`backups.schedule.${value}`)}
              </option>
            ))}
          </SelectField>
          <Field
            label={t('backups.keepLabel')}
            type="number"
            min={2}
            max={50}
            defaultValue={data.keep}
            onBlur={(event) => {
              const keep = Number(event.target.value)
              if (keep >= 2 && keep <= 50 && keep !== data.keep) void saveSettings({ backup_keep: keep })
            }}
          />
          <p className="text-xs leading-relaxed text-mist-500">{t('backups.keepHint')}</p>
        </div>
      </Section>

      <Section
        title={t('backups.listTitle')}
        aside={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Symbol name="plus" className="h-3.5 w-3.5" />
            {t('backups.createNow')}
          </Button>
        }
      >
        {data.entries.length === 0 ? (
          <p className="text-sm text-mist-500">{t('backups.empty')}</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-ink-700">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <thead className="border-b border-ink-700 bg-ink-900 text-xs tracking-wide text-mist-600 uppercase">
                <tr>
                  <th className="px-4 py-3 font-medium">{t('backups.colWhen')}</th>
                  <th className="px-4 py-3 font-medium">{t('backups.colKind')}</th>
                  <th className="px-4 py-3 font-medium">{t('backups.colNote')}</th>
                  <th className="px-4 py-3 font-medium">{t('backups.colVersion')}</th>
                  <th className="px-4 py-3 text-right font-medium">{t('backups.colSize')}</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {data.entries.map((backup) => (
                  <tr key={backup.name} className="border-b border-ink-800 last:border-0">
                    <td className="px-4 py-3 whitespace-nowrap text-mist-100">{formatDateTime(backup.created)}</td>
                    <td className="px-4 py-3">
                      <Badge tone={backup.kind === 'manual' ? 'accent' : 'neutral'}>{t(`backups.kind.${backup.kind}`)}</Badge>
                    </td>
                    <td className="px-4 py-3 text-mist-500">{backup.note || '–'}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-mist-500 tabular-nums">
                      {backup.version || '–'}
                      {!backup.compatible && (
                        <Badge tone="warn">{t(backup.reason === 'backup_newer' ? 'backups.tooNew' : 'backups.unknownVersion')}</Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap text-mist-500 tabular-nums">{formatSize(backup.size)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          aria-label={t('backups.download')}
                          title={t('backups.download')}
                          onClick={() => {
                            setPassword('')
                            setRepeat('')
                            setDownload(backup)
                          }}
                          className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-accent-600 hover:text-accent-400"
                        >
                          <Symbol name="download" />
                        </button>
                        <button
                          type="button"
                          aria-label={t('backups.delete')}
                          title={t('backups.delete')}
                          onClick={() => setRemove(backup)}
                          className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-bad-500 hover:text-bad-500"
                        >
                          <Symbol name="trash" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs leading-relaxed text-mist-600">{t('backups.folderHint', { folder: data.folder })}</p>
      </Section>

      <RestoreSection />

      <Dialog
        open={creating}
        title={t('backups.createTitle')}
        onClose={() => setCreating(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              {t('common.cancel')}
            </Button>
            <Button loading={busy} onClick={() => void create()}>
              {t('backups.createNow')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-500">{t('backups.createSub')}</p>
        <Field label={t('backups.noteLabel')} hint={t('backups.noteHint')} value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} autoFocus />
      </Dialog>

      <Dialog
        open={download !== null}
        title={t('backups.downloadTitle')}
        onClose={() => setDownload(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDownload(null)}>
              {t('common.cancel')}
            </Button>
            <Button disabled={!passwordOk} loading={busy} onClick={() => void fetchArchive()}>
              <Symbol name="download" />
              {t('backups.download')}
            </Button>
          </>
        }
      >
        <p className="font-mono text-xs text-mist-500">{download?.name}</p>
        <p className="text-sm leading-relaxed text-mist-300">{t('backups.downloadWhat')}</p>
        <Banner tone="warn">{t('backups.passwordWarning')}</Banner>
        <Field label={t('backups.passwordLabel')} type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
        <Field
          label={t('backups.passwordRepeat')}
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={(event) => setRepeat(event.target.value)}
          error={repeat.length > 0 && password !== repeat ? t('backups.passwordMismatch') : null}
          hint={password.length > 0 && password.length < MIN_PASSWORD ? t('backups.passwordTooShort', { min: MIN_PASSWORD }) : undefined}
        />
      </Dialog>

      <Dialog
        open={remove !== null}
        title={t('backups.deleteTitle')}
        onClose={() => setRemove(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemove(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void deleteBackup()}>
              {t('backups.delete')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{remove && t('backups.deleteText', { when: formatDateTime(remove.created), note: remove.note || '–' })}</p>
        <Banner tone="warn">{t('backups.deleteWarning')}</Banner>
      </Dialog>
    </div>
  )
}

/**
 * Restore, deliberately at the bottom and deliberately collapsed: the only button on this page that takes something away.
 * Two steps: first look at what is in the archive, then decide. Like nexcrate, the restore happens
 * on the next start: nextrmnl shuts itself down, Docker starts it again.
 */
function RestoreSection() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [brief, setBrief] = useState<BackupBrief | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [restarting, setRestarting] = useState(false)

  function form(): FormData {
    const data = new FormData()
    data.append('file', file as File)
    data.append('password', password)
    return data
  }

  async function check() {
    setBusy(true)
    setError(null)
    try {
      setBrief(await api.post<BackupBrief>('/api/backups/check', form()))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function restore() {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/backups/restore', form())
      setRestarting(true)
      // The server restarts. As soon as it responds again, we reload the page; you land on the sign-in page.
      const started = Date.now()
      const poll = window.setInterval(() => {
        void fetch('/api/health', { cache: 'no-store' })
          .then((response) => {
            if (response.ok && Date.now() - started > 3000) {
              window.clearInterval(poll)
              window.location.reload()
            }
          })
          .catch(() => undefined)
      }, 2000)
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  if (restarting) {
    return (
      <Card className="border-bad-500/30">
        <Banner tone="warn">{t('restore.restarting')}</Banner>
      </Card>
    )
  }

  return (
    <Card className="flex flex-col gap-4 border-bad-500/30">
      <div>
        <h2 className="text-lg font-semibold">{t('restore.title')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('restore.intro')}</p>
      </div>
      {!open ? (
        <div>
          <Button variant="ghost" onClick={() => setOpen(true)}>
            {t('restore.open')}
          </Button>
        </div>
      ) : (
        <>
          <FileField
            label={t('restore.fileLabel')}
            accept=".zip,application/zip"
            onChange={(next) => {
              setFile(next)
              setBrief(null)
            }}
          />
          <Field
            label={t('restore.passwordLabel')}
            type="password"
            autoComplete="off"
            value={password}
            hint={t('restore.passwordHint')}
            onChange={(event) => {
              setPassword(event.target.value)
              setBrief(null)
            }}
          />
          {error && <Banner tone="bad">{error}</Banner>}
          {brief === null ? (
            <div className="flex flex-wrap gap-3">
              <Button disabled={!file || password.length === 0} loading={busy} onClick={() => void check()}>
                {t('restore.check')}
              </Button>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t('common.cancel')}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900 p-4">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-mist-600">{t('restore.fromWhen')}</dt>
                <dd className="text-mist-100">{formatDateTime(brief.created)}</dd>
                <dt className="text-mist-600">{t('restore.fromVersion')}</dt>
                <dd className="text-mist-100 tabular-nums">{brief.version || '–'}</dd>
                <dt className="text-mist-600">{t('restore.note')}</dt>
                <dd className="text-mist-100">{brief.note || '–'}</dd>
                <dt className="text-mist-600">{t('restore.contents')}</dt>
                <dd className="text-mist-100">{t('restore.contentsText', { accounts: brief.accounts, connections: brief.connections })}</dd>
              </dl>
              {!brief.compatible && <Banner tone="bad">{t(brief.reason === 'backup_newer' ? 'restore.tooNew' : 'restore.unknownVersion', { version: brief.version })}</Banner>}
              {brief.key_from_env && <Banner tone="warn">{t('restore.envKey')}</Banner>}
              {!brief.key_in_archive && !brief.key_from_env && <Banner tone="bad">{t('restore.noKey')}</Banner>}
              <Banner tone="bad">
                <p>{t('restore.vaultsWarning')}</p>
                <p className="mt-1">
                  <Link to="/vault" className="font-semibold underline">
                    {t('restore.vaultsLink')}
                  </Link>
                </p>
              </Banner>
              <Banner tone="warn">{t('restore.sessionsWarning')}</Banner>
              <div className="flex flex-wrap gap-3">
                <Button variant="ghost" onClick={() => setBrief(null)}>
                  {t('common.cancel')}
                </Button>
                <Button variant="danger" disabled={!brief.compatible} loading={busy} onClick={() => void restore()}>
                  {t('restore.restoreNow')}
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  )
}
