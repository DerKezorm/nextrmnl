import { useTranslation } from 'react-i18next'

import { useWorkspace, type Session } from '../../state/workspace'
import { Symbol } from '../Symbol'

function StatusDot({ session }: { session: Session }) {
  const tone = {
    connecting: 'bg-warn-500 animate-pulse',
    open: 'bg-ok-500',
    closed: 'bg-mist-600',
    failed: 'bg-bad-500',
  }[session.status]
  return <span className={'h-1.5 w-1.5 shrink-0 rounded-full ' + tone} aria-hidden="true" />
}

/** The tabs for the open sessions, with the buttons for the active one on the right. */
export function SessionTabs({
  showListButton,
  listOpen,
  onToggleList,
  onReconnect,
  fullscreen,
  onToggleFullscreen,
}: {
  showListButton: boolean
  listOpen: boolean
  onToggleList: () => void
  onReconnect: (id: string) => void
  fullscreen: boolean
  onToggleFullscreen: () => void
}) {
  const { t } = useTranslation()
  const { sessions, activeId, activate, closeSession, toggleFiles } = useWorkspace()
  const active = sessions.find((s) => s.id === activeId)

  const toolButton = 'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors'

  return (
    <div className="flex items-center gap-2 border-b border-ink-700 bg-ink-900/40 px-2 py-1.5">
      {showListButton && (
        <button
          type="button"
          onClick={onToggleList}
          aria-expanded={listOpen}
          aria-label={t('list.title')}
          className={
            'inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ' +
            (listOpen ? 'bg-accent-500/15 text-accent-400' : 'text-mist-400 hover:bg-ink-800 hover:text-mist-100')
          }
        >
          <Symbol name="list" />
          <span className="hidden sm:inline">{t('list.title')}</span>
        </button>
      )}
      <div className="nt-scroll flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" role="tablist" aria-label={t('tabs.label')}>
        {sessions.map((session) => {
          const selected = session.id === activeId
          return (
            <div
              key={session.id}
              className={
                'group flex shrink-0 items-center rounded-full border transition-colors ' +
                (selected ? 'border-accent-500/50 bg-accent-500/12' : 'border-transparent hover:bg-ink-800')
              }
            >
              <button
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => activate(session.id)}
                className={'flex items-center gap-2 py-1.5 pr-1 pl-3 text-sm font-medium ' + (selected ? 'text-accent-400' : 'text-mist-400 hover:text-mist-100')}
              >
                <StatusDot session={session} />
                {session.name}
              </button>
              <button
                type="button"
                onClick={() => closeSession(session.id)}
                aria-label={t('tabs.close', { name: session.name })}
                title={t('tabs.close', { name: session.name })}
                className="mr-1 rounded-full p-1 text-mist-600 hover:bg-ink-700 hover:text-mist-100"
              >
                <Symbol name="close" className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
      </div>

      {active && (
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => toggleFiles(active.id)}
            aria-pressed={active.filesOpen}
            disabled={active.status !== 'open'}
            className={
              toolButton +
              ' disabled:cursor-not-allowed disabled:opacity-40 ' +
              (active.filesOpen ? 'bg-accent-500/15 text-accent-400' : 'text-mist-400 hover:bg-ink-800 hover:text-mist-100')
            }
          >
            <Symbol name="files" />
            <span className="hidden sm:inline">{t('tabs.files')}</span>
          </button>
          <button type="button" onClick={() => onReconnect(active.id)} className={toolButton + ' text-mist-400 hover:bg-ink-800 hover:text-mist-100'} title={t('tabs.reconnect')}>
            <Symbol name="refresh" />
            <span className="hidden lg:inline">{t('tabs.reconnect')}</span>
          </button>
          <button type="button" onClick={() => closeSession(active.id)} className={toolButton + ' text-mist-400 hover:bg-bad-500/10 hover:text-bad-500'} title={t('tabs.disconnect')}>
            <Symbol name="plug" />
            <span className="hidden lg:inline">{t('tabs.disconnect')}</span>
          </button>
          <button
            type="button"
            onClick={onToggleFullscreen}
            aria-pressed={fullscreen}
            className={toolButton + ' text-mist-400 hover:bg-ink-800 hover:text-mist-100'}
            title={fullscreen ? t('tabs.fullscreenOff') : t('tabs.fullscreen')}
            aria-label={fullscreen ? t('tabs.fullscreenOff') : t('tabs.fullscreen')}
          >
            <Symbol name={fullscreen ? 'shrink' : 'expand'} />
          </button>
        </div>
      )}
    </div>
  )
}
