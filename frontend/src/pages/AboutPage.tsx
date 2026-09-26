import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { api, errorMessage } from '../api/client'
import type { AboutInfo } from '../api/types'
import { useAuth } from '../auth'
import { Logo } from '../components/Logo'
import { useNotice } from '../components/Notice'
import { Symbol } from '../components/Symbol'
import { Banner, Button, Card, PageHeader, PageLoading } from '../components/ui'
import { formatDateTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/**
 * The other public nex apps. Listed here in nextrmnl only, not carried into the other apps.
 * nexapps.dev joins once the umbrella site is online.
 */
const APPS = [
  { key: 'nexview', site: 'https://nexview.nexapps.dev', repo: 'https://github.com/DerKezorm/nexview' },
  { key: 'nexmail', site: 'https://nexmail.nexapps.dev', repo: 'https://github.com/DerKezorm/nexmail' },
  { key: 'nexdeck', site: 'https://nexdeck.nexapps.dev', repo: 'https://github.com/DerKezorm/nexdeck' },
  { key: 'nexcrate', site: 'https://nexcrate.nexapps.dev', repo: 'https://github.com/DerKezorm/nexcrate' },
  { key: 'nexbeat', site: 'https://nexbeat.nexapps.dev', repo: 'https://github.com/DerKezorm/nexbeat' },
  { key: 'nexpulse', repo: 'https://github.com/DerKezorm/nexpulse' },
] as const

function External({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-accent-400 hover:underline">
      {children}
      <Symbol name="external" className="h-3.5 w-3.5" />
    </a>
  )
}

export function AboutPage() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { account } = useAuth()
  const about = useLoad(() => api.get<AboutInfo>('/api/about'))
  const [checking, setChecking] = useState(false)
  const operator = account?.role === 'operator'

  async function check() {
    setChecking(true)
    try {
      about.set(await api.post<AboutInfo>('/api/about/check'))
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setChecking(false)
    }
  }

  const info = about.data
  if (!info) return about.error ? <Banner tone="bad">{about.error}</Banner> : <PageLoading />

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={t('about.title')} />
      <Card>
        <div className="flex flex-wrap items-center gap-4">
          <Logo className="h-14 w-14" />
          <div>
            <p className="text-2xl font-bold tracking-tight">
              NEX<span className="text-accent-500">TRMNL</span> <span className="text-base font-medium text-mist-500 tabular-nums">v{info.version}</span>
            </p>
            <p className="text-sm text-mist-500">{t('about.tagline')}</p>
          </div>
        </div>
        <div className="mt-5 flex flex-col gap-3 border-t border-ink-700 pt-4">
          {!info.update_check ? (
            <p className="text-sm text-mist-500">
              {t('about.checkOff')}{' '}
              {operator && (
                <Link to="/settings?tab=security" className="text-accent-400 hover:underline">
                  {t('about.checkOffLink')}
                </Link>
              )}
            </p>
          ) : info.update_available ? (
            <Banner tone="ok">
              {t('about.updateAvailable', { version: info.latest_version })}{' '}
              <a href={info.release_url} target="_blank" rel="noreferrer" className="font-semibold underline">
                {t('about.releaseNotes')}
              </a>
            </Banner>
          ) : (
            <p className="text-sm text-mist-500">
              {info.update_checked && info.checked_at
                ? info.latest_version
                  ? t('about.upToDate', { when: formatDateTime(info.checked_at) })
                  : t('about.noRelease', { when: formatDateTime(info.checked_at) })
                : t('about.notChecked')}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {info.update_check && operator && (
              <Button variant="ghost" size="sm" loading={checking} onClick={() => void check()}>
                <Symbol name="refresh" />
                {t('about.checkNow')}
              </Button>
            )}
            <span className="px-2">
              <External href={info.repo_url}>GitHub</External>
            </span>
            <span className="px-2">
              <External href={info.release_url}>{t('about.releases')}</External>
            </span>
            <span className="px-2">
              <External href="/api/docs">{t('about.apiDocs')}</External>
            </span>
          </div>
          <p className="text-xs text-mist-600">{t('about.license', { license: info.license })}</p>
        </div>
      </Card>

      <Card>
        <h2 className="text-lg font-semibold">{t('about.appsTitle')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('about.appsIntro')}</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {APPS.map((app) => (
            <div key={app.key} className="flex flex-col rounded-xl border border-ink-700 bg-ink-900 p-4">
              <p className="font-semibold">
                nex<span className="text-accent-500">{app.key.slice(3)}</span>
              </p>
              <p className="mt-1 flex-1 text-sm text-mist-500">{t(`about.app.${app.key}`)}</p>
              <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
                {'site' in app && (
                  <li>
                    <External href={app.site}>{t('about.site')}</External>
                  </li>
                )}
                <li>
                  <External href={app.repo}>{t('about.source')}</External>
                </li>
              </ul>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
