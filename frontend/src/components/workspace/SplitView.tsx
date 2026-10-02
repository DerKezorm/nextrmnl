import { useTranslation } from 'react-i18next'

import { useWorkspace } from '../../state/workspace'
import { TerminalHost } from './TerminalHost'

const GRID = {
  single: 'grid-cols-1 grid-rows-1',
  columns: 'grid-cols-2 grid-rows-1',
  rows: 'grid-cols-1 grid-rows-2',
  grid: 'grid-cols-2 grid-rows-2',
} as const

/**
 * Several terminals at once. Each field shows one session and has a small choice above it; the tabs stay as they
 * are. The field of the active session is marked; with "type into all" on, every field gets a red frame.
 */
export function SplitView() {
  const { t } = useTranslation()
  const { layout, panes, sessions, activeId, showInPane, broadcast } = useWorkspace()

  return (
    <div className={'grid min-h-0 flex-1 gap-px bg-ink-700 ' + GRID[layout]}>
      {panes.map((id, index) => {
        const session = sessions.find((s) => s.id === id) ?? null
        const focused = session !== null && session.id === activeId
        const frame = broadcast && session ? 'ring-2 ring-bad-500 ring-inset' : focused ? 'ring-1 ring-accent-500/70 ring-inset' : ''
        return (
          <div key={index} className={'relative flex min-h-0 min-w-0 flex-col bg-term-bg ' + frame} data-pane={index}>
            <div className="flex items-center gap-2 border-b border-ink-700 bg-ink-900/60 px-2 py-1">
              <label className="sr-only" htmlFor={`pane-${index}`}>
                {t('split.paneLabel', { number: index + 1 })}
              </label>
              <select
                id={`pane-${index}`}
                value={session?.id ?? ''}
                onChange={(event) => showInPane(index, event.target.value || null)}
                className={'min-w-0 max-w-full truncate rounded-md bg-transparent py-0.5 pr-6 pl-1 text-xs font-medium focus:outline-none ' + (focused ? 'text-accent-400' : 'text-mist-300')}
              >
                <option value="">{t('split.emptyPane')}</option>
                {sessions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
              </select>
            </div>
            {session ? (
              <TerminalHost key={session.id} session={session} visible focused={focused} />
            ) : (
              <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-mist-500">{t('split.emptyPaneHint')}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}
