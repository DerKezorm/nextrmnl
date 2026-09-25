import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, CSRF_HEADER, api, errorMessage, translateError } from '../../api/client'
import type { FileEntry, FileListing } from '../../api/types'
import { formatDate, formatNumber } from '../../lib/format'
import { useWorkspace, type Session } from '../../state/workspace'
import { Dialog } from '../Dialog'
import { Symbol } from '../Symbol'
import { Banner, Button, Field } from '../ui'

function formatSize(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000
    unit += 1
  }
  return `${formatNumber(value, unit === 0 ? 0 : 1)} ${units[unit]}`
}

/** 0o755 as "rwxr-xr-x", the way ls shows it. */
function formatMode(mode: number | null, type: FileEntry['type']): string {
  if (mode === null) return ''
  const bits = 'rwxrwxrwx'
  let text = type === 'dir' ? 'd' : type === 'link' ? 'l' : '-'
  for (let i = 0; i < 9; i += 1) text += mode & (1 << (8 - i)) ? bits[i] : '-'
  return text
}

function join(base: string, name: string): string {
  return base.endsWith('/') ? base + name : `${base}/${name}`
}

function parent(path: string): string {
  const trimmed = path.replace(/\/+$/, '')
  const index = trimmed.lastIndexOf('/')
  return index <= 0 ? '/' : trimmed.slice(0, index)
}

/** The sentence for the error code, followed in parentheses by what the SSH server itself said. */
function describe(caught: unknown): string {
  const reason = caught instanceof ApiError && typeof caught.data?.reason === 'string' ? caught.data.reason : ''
  return errorMessage(caught) + (reason ? ` (${reason})` : '')
}

interface Transfer {
  id: number
  name: string
  progress: number
  error: string | null
}

type Action = { kind: 'mkdir' } | { kind: 'rename'; entry: FileEntry } | { kind: 'delete'; entry: FileEntry }

let transferCounter = 0

/** Files on the session's host, over the same SSH connection (SFTP). */
export function FilePanel({ session }: { session: Session }) {
  const { t } = useTranslation()
  const { toggleFiles } = useWorkspace()
  const base = `/api/sessions/${encodeURIComponent(session.serverId ?? '')}/files`
  const [listing, setListing] = useState<FileListing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [dragging, setDragging] = useState(false)
  const [transfers, setTransfers] = useState<Transfer[]>([])
  const [action, setAction] = useState<Action | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  // Inside the dialog itself: a message at the bottom of the page sits behind the dialog and goes unseen.
  const [actionError, setActionError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(
    async (path: string) => {
      setLoading(true)
      try {
        setListing(await api.get<FileListing>(`${base}?path=${encodeURIComponent(path)}`))
        setError(null)
      } catch (caught) {
        setError(describe(caught))
      } finally {
        setLoading(false)
      }
    },
    [base],
  )

  useEffect(() => {
    void load('')
  }, [load])

  const path = listing?.path ?? ''
  const home = listing?.home ?? ''
  const crumbs = path.split('/').filter(Boolean)

  function download(entry: FileEntry) {
    const anchor = document.createElement('a')
    anchor.href = `${base}/download?path=${encodeURIComponent(join(path, entry.name))}`
    anchor.download = entry.name
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }

  function upload(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      transferCounter += 1
      const id = transferCounter
      setTransfers((current) => [...current, { id, name: file.name, progress: 0, error: null }])
      const request = new XMLHttpRequest()
      request.open('POST', `${base}/upload?path=${encodeURIComponent(path)}`)
      for (const [key, value] of Object.entries(CSRF_HEADER)) request.setRequestHeader(key, value)
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) setTransfers((current) => current.map((tr) => (tr.id === id ? { ...tr, progress: event.loaded / event.total } : tr)))
      }
      request.onload = () => {
        if (request.status >= 200 && request.status < 300) {
          setTransfers((current) => current.filter((tr) => tr.id !== id))
          void load(path)
          return
        }
        let message = t('errors.http', { status: request.status })
        try {
          const detail = JSON.parse(request.responseText)?.detail
          if (detail && typeof detail === 'object') message = translateError(detail, request.status)
        } catch {
          /* not JSON */
        }
        setTransfers((current) => current.map((tr) => (tr.id === id ? { ...tr, error: message } : tr)))
      }
      request.onerror = () => setTransfers((current) => current.map((tr) => (tr.id === id ? { ...tr, error: t('errors.network') } : tr)))
      const body = new FormData()
      body.append('file', file)
      request.send(body)
    }
  }

  async function runAction() {
    if (!action) return
    setBusy(true)
    setActionError(null)
    try {
      if (action.kind === 'mkdir') await api.post(`${base}/mkdir`, { path: join(path, name.trim()) })
      else if (action.kind === 'rename') await api.post(`${base}/rename`, { path: join(path, action.entry.name), to: join(path, name.trim()) })
      else {
        // A folder goes together with its contents; the dialog said so beforehand.
        const recursive = action.entry.type === 'dir' ? '&recursive=true' : ''
        await api.delete(`${base}?path=${encodeURIComponent(join(path, action.entry.name))}${recursive}`)
      }
      setAction(null)
      await load(path)
    } catch (caught) {
      setActionError(describe(caught))
    } finally {
      setBusy(false)
    }
  }

  const openAction = (next: Action) => {
    setName(next.kind === 'rename' ? next.entry.name : '')
    setActionError(null)
    setAction(next)
  }

  const toolButton = 'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-mist-300 hover:bg-ink-800 hover:text-mist-100'

  return (
    <aside
      className="relative flex w-full shrink-0 flex-col border-l border-ink-700 bg-ink-900/70 md:w-[22rem]"
      aria-label={t('files.title')}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        if (event.dataTransfer.files.length) upload(event.dataTransfer.files)
      }}
    >
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <Symbol name="files" className="h-4 w-4 text-accent-400" />
        <h2 className="text-sm font-semibold">{t('files.title')}</h2>
        <span className="text-xs text-mist-600">SFTP</span>
        <button type="button" onClick={() => toggleFiles(session.id)} className="ml-auto rounded-full p-1 text-mist-500 hover:bg-ink-800 hover:text-mist-100" aria-label={t('files.close')}>
          <Symbol name="close" />
        </button>
      </div>

      <nav className="flex flex-wrap items-center gap-1 border-b border-ink-700 px-3 py-2 font-mono text-xs" aria-label={t('files.path')}>
        <button type="button" onClick={() => void load('/')} className={'rounded px-1 hover:bg-ink-800 ' + (path === '/' ? 'text-mist-100' : 'text-mist-500')}>
          /
        </button>
        {crumbs.map((crumb, index) => {
          const target = '/' + crumbs.slice(0, index + 1).join('/')
          return (
            <span key={target} className="flex items-center gap-1">
              {index > 0 && <span className="text-mist-600">/</span>}
              <button type="button" onClick={() => void load(target)} className={'rounded px-1 hover:bg-ink-800 ' + (target === path ? 'text-mist-100' : 'text-mist-500')}>
                {crumb}
              </button>
            </span>
          )
        })}
      </nav>

      <div className="flex items-center gap-1 border-b border-ink-700 px-2 py-1.5">
        <button type="button" onClick={() => fileInput.current?.click()} className={toolButton}>
          <Symbol name="upload" className="h-3.5 w-3.5" />
          {t('files.upload')}
        </button>
        <input ref={fileInput} type="file" multiple className="hidden" onChange={(event) => event.target.files && upload(event.target.files)} />
        <button type="button" onClick={() => openAction({ kind: 'mkdir' })} className={toolButton}>
          <Symbol name="folderPlus" className="h-3.5 w-3.5" />
          {t('files.newFolder')}
        </button>
        {home && path !== home && (
          <button type="button" onClick={() => void load('')} className={toolButton} title={home}>
            <Symbol name="folder" className="h-3.5 w-3.5" />
            {t('files.home')}
          </button>
        )}
        {path && path !== '/' && (
          <button type="button" onClick={() => void load(parent(path))} className="ml-auto inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs text-mist-500 hover:bg-ink-800 hover:text-mist-100">
            <Symbol name="up" className="h-3.5 w-3.5" />
            {t('files.up')}
          </button>
        )}
      </div>

      <ul className="nt-scroll min-h-0 flex-1 overflow-y-auto py-1">
        {error && (
          <li className="p-3">
            <Banner tone="bad">{error}</Banner>
          </li>
        )}
        {!error && !loading && listing?.entries.length === 0 && <li className="px-4 py-8 text-center text-sm text-mist-500">{t('files.empty')}</li>}
        {(listing?.entries ?? []).map((entry) => {
          const dir = entry.type === 'dir'
          return (
            <li key={entry.name} className="group">
              <div className="flex items-center gap-2.5 px-3 py-1.5 hover:bg-ink-800">
                <button type="button" onClick={() => (dir ? void load(join(path, entry.name)) : download(entry))} className="flex min-w-0 flex-1 items-center gap-2.5 text-left" title={formatMode(entry.mode, entry.type)}>
                  <Symbol name={dir ? 'folder' : 'file'} className={'h-4 w-4 shrink-0 ' + (dir ? 'text-accent-400' : 'text-mist-500')} />
                  <span className={'truncate text-sm ' + (entry.name.startsWith('.') ? 'text-mist-500' : 'text-mist-200')}>{entry.name}</span>
                </button>
                <span className="shrink-0 text-[11px] text-mist-600 tabular-nums group-hover:hidden">{dir ? (entry.mtime ? formatDate(new Date(entry.mtime * 1000)) : '') : formatSize(entry.size)}</span>
                <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex">
                  {!dir && (
                    <button type="button" onClick={() => download(entry)} className="rounded-full p-1 text-mist-500 hover:bg-ink-700 hover:text-mist-100" aria-label={t('files.download', { name: entry.name })} title={t('files.download', { name: entry.name })}>
                      <Symbol name="download" className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <button type="button" onClick={() => openAction({ kind: 'rename', entry })} className="rounded-full p-1 text-mist-500 hover:bg-ink-700 hover:text-mist-100" aria-label={t('files.rename', { name: entry.name })} title={t('files.rename', { name: entry.name })}>
                    <Symbol name="pencil" className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" onClick={() => openAction({ kind: 'delete', entry })} className="rounded-full p-1 text-mist-500 hover:bg-bad-500/10 hover:text-bad-500" aria-label={t('files.delete', { name: entry.name })} title={t('files.delete', { name: entry.name })}>
                    <Symbol name="trash" className="h-3.5 w-3.5" />
                  </button>
                </span>
              </div>
            </li>
          )
        })}
      </ul>

      {transfers.length > 0 && (
        <div className="border-t border-ink-700 px-3 py-2.5">
          <p className="mb-1.5 text-[11px] font-semibold tracking-wide text-mist-500 uppercase">{t('files.transfers')}</p>
          {transfers.map((transfer) => (
            <div key={transfer.id} className="mb-2">
              <div className="flex items-center gap-2 text-xs">
                <Symbol name="upload" className="h-3.5 w-3.5 shrink-0 text-accent-400" />
                <span className="min-w-0 flex-1 truncate text-mist-300">{transfer.name}</span>
                {transfer.error ? (
                  <button type="button" onClick={() => setTransfers((current) => current.filter((tr) => tr.id !== transfer.id))} className="shrink-0 text-bad-500">
                    {transfer.error}
                  </button>
                ) : (
                  <span className="shrink-0 text-mist-500 tabular-nums">{Math.round(transfer.progress * 100)} %</span>
                )}
              </div>
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-ink-700">
                <div className={'h-full rounded-full ' + (transfer.error ? 'bg-bad-500' : 'bg-accent-500')} style={{ width: `${Math.round(transfer.progress * 100)}%` }} />
              </div>
            </div>
          ))}
        </div>
      )}

      {dragging && (
        <div className="pointer-events-none absolute inset-2 flex items-center justify-center rounded-xl border-2 border-dashed border-accent-500 bg-ink-950/80 text-sm font-medium text-accent-400">
          {t('files.dropHere', { path })}
        </div>
      )}

      <Dialog
        open={action !== null}
        title={action?.kind === 'mkdir' ? t('files.newFolder') : action?.kind === 'rename' ? t('files.rename', { name: action.entry.name }) : t('files.delete', { name: action?.kind === 'delete' ? action.entry.name : '' })}
        onClose={() => setAction(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setAction(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant={action?.kind === 'delete' ? 'danger' : 'primary'} loading={busy} disabled={action?.kind !== 'delete' && !name.trim()} onClick={() => void runAction()}>
              {action?.kind === 'delete' ? (action.entry.type === 'dir' ? t('files.deleteDirNow') : t('files.deleteNow')) : action?.kind === 'rename' ? t('files.renameNow') : t('files.createNow')}
            </Button>
          </>
        }
      >
        {actionError && <Banner tone="bad">{actionError}</Banner>}
        {action?.kind === 'delete' ? (
          <p className="text-sm text-mist-300">{action.entry.type === 'dir' ? t('files.deleteDirText', { name: action.entry.name }) : t('files.deleteText', { name: action.entry.name })}</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (name.trim()) void runAction()
            }}
          >
            <Field label={t('files.name')} value={name} onChange={(event) => setName(event.target.value)} autoFocus spellCheck={false} />
          </form>
        )}
      </Dialog>
    </aside>
  )
}
