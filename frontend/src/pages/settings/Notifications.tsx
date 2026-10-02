import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { NotifyCategory, NotifyKind, Settings } from '../../api/types'
import { useNotice } from '../../components/Notice'
import { Banner, Button, Field, Section, SelectField, Switch } from '../../components/ui'

const CATEGORIES: NotifyCategory[] = ['security', 'signin', 'sessions', 'operations']

/**
 * Messages to nexsift, Gotify, ntfy or a webhook. A way out, so it is closed until the operator opens it here.
 * Address, kind and token are saved together with the button; the token is never shown again, only whether one is
 * stored.
 */
export function NotificationsSection({ data, onSave }: { data: Settings; onSave: (values: Partial<Settings> & { notify_token?: string }) => Promise<void> }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [kind, setKind] = useState<NotifyKind>(data.notify_kind)
  const [url, setUrl] = useState(data.notify_url)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const dirty = kind !== data.notify_kind || url.trim() !== data.notify_url || token !== ''

  async function saveTarget() {
    setBusy(true)
    try {
      await onSave({ notify_kind: kind, notify_url: url.trim(), ...(token ? { notify_token: token } : {}) })
      setToken('')
    } finally {
      setBusy(false)
    }
  }

  async function sendTest() {
    setTesting(true)
    try {
      await api.post('/api/settings/notify/test')
      notify(t('notifications.testSent'))
    } catch (caught) {
      notify(errorMessage(caught))
    } finally {
      setTesting(false)
    }
  }

  const toggleCategory = (category: NotifyCategory, on: boolean) => {
    const next = on ? [...data.notify_events, category] : data.notify_events.filter((c) => c !== category)
    void onSave({ notify_events: CATEGORIES.filter((c) => next.includes(c)) })
  }

  return (
    <Section title={t('notifications.title')} intro={t('notifications.lead')}>
      <div className="grid gap-4 sm:grid-cols-[12rem_1fr]">
        <SelectField label={t('notifications.kind')} value={kind} onChange={(value) => setKind(value as NotifyKind)}>
          <option value="gotify">{t('notifications.kindGotify')}</option>
          <option value="ntfy">{t('notifications.kindNtfy')}</option>
          <option value="webhook">{t('notifications.kindWebhook')}</option>
        </SelectField>
        <Field
          label={t('notifications.url')}
          hint={t(`notifications.urlHint.${kind}`)}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder={kind === 'ntfy' ? 'https://ntfy.example.com/homelab' : kind === 'webhook' ? 'https://inbox.example.com/hook' : 'https://nexsift.example.com:8491'}
          spellCheck={false}
          autoComplete="off"
        />
      </div>
      <Field
        label={t('notifications.token')}
        hint={data.notify_token_set ? t('notifications.tokenSet') : t(`notifications.tokenHint.${kind}`)}
        type="password"
        value={token}
        onChange={(event) => setToken(event.target.value)}
        placeholder={data.notify_token_set ? '••••••••' : ''}
        autoComplete="new-password"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" loading={busy} disabled={!dirty} onClick={() => void saveTarget()}>
          {t('common.save')}
        </Button>
        {data.notify_token_set && (
          <Button variant="ghost" size="sm" onClick={() => void onSave({ notify_token: '' })}>
            {t('notifications.removeToken')}
          </Button>
        )}
        <Button variant="ghost" size="sm" loading={testing} disabled={!data.notify_enabled || dirty} onClick={() => void sendTest()}>
          {t('notifications.test')}
        </Button>
      </div>

      <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
        <Switch
          label={t('notifications.enabled')}
          hint={t('notifications.enabledHint')}
          checked={data.notify_enabled}
          onChange={(value) => void onSave({ notify_enabled: value })}
        />
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium text-mist-300">{t('notifications.events')}</legend>
          {CATEGORIES.map((category) => (
            <label key={category} className="flex cursor-pointer items-start gap-3 rounded-xl border border-ink-700 p-3 hover:bg-ink-800">
              <input
                type="checkbox"
                className="mt-0.5 accent-accent-500"
                checked={data.notify_events.includes(category)}
                onChange={(event) => toggleCategory(category, event.target.checked)}
              />
              <span>
                <span className="block text-sm text-mist-100">{t(`notifications.category.${category}`)}</span>
                <span className="block text-xs text-mist-500">{t(`notifications.categoryHint.${category}`)}</span>
              </span>
            </label>
          ))}
        </fieldset>
      </div>
      <Banner>{t('notifications.never')}</Banner>
    </Section>
  )
}
