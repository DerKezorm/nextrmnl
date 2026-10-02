import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { Symbol } from '../components/Symbol'
import { Banner, Button, Card, PageLoading } from '../components/ui'
import { useWorkspace } from '../state/workspace'

/**
 * A direct link to a saved connection (`/connect/12`), for bookmarks and dashboard cards. It asks before it
 * connects: a link someone else hands over must never open a shell by itself. Only connections the signed-in
 * account sees are offered; anything else looks like an unknown link.
 */
export function ConnectPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { id } = useParams()
  const { connections, connectionsError, connectionsLoaded, openConnection } = useWorkspace()
  const connectionId = Number(id)
  const connection = Number.isInteger(connectionId) ? connections.find((c) => c.id === connectionId) : undefined
  if (!connectionsLoaded) return <PageLoading />

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 items-start pt-16">
      <Card className="flex w-full flex-col gap-4">
        {connection ? (
          <>
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-500/15 text-accent-400">
                <Symbol name="terminal" className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <h1 className="truncate text-lg font-semibold text-mist-100">{t('connect.title', { name: connection.name })}</h1>
                <p className="truncate font-mono text-xs text-mist-500">
                  {connection.user}@{connection.host}
                  {connection.port !== 22 ? `:${connection.port}` : ''}
                </p>
              </div>
            </div>
            <p className="text-sm text-mist-300">{t('connect.lead')}</p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => navigate('/', { replace: true })}>
                {t('common.cancel')}
              </Button>
              <Button
                autoFocus
                onClick={() => {
                  openConnection(connection.id)
                  navigate('/', { replace: true })
                }}
              >
                {t('connect.go')}
              </Button>
            </div>
          </>
        ) : (
          <>
            <h1 className="text-lg font-semibold text-mist-100">{t('connect.unknownTitle')}</h1>
            {connectionsError ? <Banner tone="bad">{connectionsError}</Banner> : <p className="text-sm text-mist-300">{t('connect.unknown')}</p>}
            <div className="flex justify-end">
              <Link to="/" replace className="text-sm font-medium text-accent-400 hover:text-accent-300">
                {t('connect.toWorkspace')}
              </Link>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
