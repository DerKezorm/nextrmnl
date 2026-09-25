import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAuth } from '../../auth'
import { useWorkspace } from '../../state/workspace'
import { Dialog } from '../Dialog'
import { Symbol } from '../Symbol'
import { Banner, Button, Field, Switch } from '../ui'

/** The server's prompts while connecting: unknown host, changed key, password, passphrase. */
export function PromptDialogs() {
  const { t } = useTranslation()
  const { account } = useAuth()
  const { prompt, answerPrompt, sessions } = useWorkspace()
  const [secret, setSecret] = useState('')
  const [remember, setRemember] = useState(false)
  const session = sessions.find((s) => s.id === prompt?.sessionId)
  if (!prompt || !session) return null

  const where = prompt.data.host ? `${session.name} (${prompt.data.host})` : session.name
  const keyType = (prompt.data.keyType || '').replace('ssh-', '').toUpperCase()

  if (prompt.kind === 'hostkey-new') {
    return (
      <Dialog
        open
        title={t('hostkey.newTitle')}
        onClose={() => answerPrompt({ accept: false })}
        footer={
          <>
            <Button variant="ghost" onClick={() => answerPrompt({ accept: false })}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => answerPrompt({ accept: true })}>{t('hostkey.trust')}</Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('hostkey.newLead', { where })}</p>
        <div className="rounded-xl border border-ink-700 bg-ink-900 p-4">
          <p className="mb-1 flex items-center gap-2 text-xs font-semibold tracking-wide text-mist-500 uppercase">
            <Symbol name="fingerprint" className="h-4 w-4" />
            {keyType}
          </p>
          <p className="font-mono text-sm break-all text-mist-100">{prompt.data.fingerprint}</p>
        </div>
        <p className="text-xs text-mist-500">
          {t('hostkey.howToCheck')} <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-mist-300">ssh-keygen -lf /etc/ssh/ssh_host_{keyType.toLowerCase() || 'ed25519'}_key.pub</code>
        </p>
      </Dialog>
    )
  }

  if (prompt.kind === 'hostkey-changed') {
    return (
      <Dialog
        open
        title={t('hostkey.changedTitle')}
        onClose={() => answerPrompt({ accept: false })}
        footer={
          <>
            <Button variant="danger" onClick={() => answerPrompt({ accept: true })}>
              {t('hostkey.acceptNew')}
            </Button>
            <Button onClick={() => answerPrompt({ accept: false })}>{t('hostkey.dontConnect')}</Button>
          </>
        }
      >
        <div className="flex items-start gap-3 rounded-xl border border-bad-500/40 bg-bad-500/10 p-4">
          <Symbol name="shieldAlert" className="mt-0.5 h-5 w-5 shrink-0 text-bad-500" />
          <p className="text-sm text-mist-200">{t('hostkey.changedLead', { where })}</p>
        </div>
        <dl className="grid gap-3 text-sm">
          <div>
            <dt className="text-xs font-semibold tracking-wide text-mist-500 uppercase">{t('hostkey.stored')}</dt>
            <dd className="font-mono break-all text-mist-400 line-through decoration-bad-500/60">{prompt.data.storedFingerprint}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold tracking-wide text-mist-500 uppercase">{t('hostkey.offered')}</dt>
            <dd className="font-mono break-all text-mist-100">{prompt.data.fingerprint}</dd>
          </div>
        </dl>
        <p className="text-xs text-mist-500">{t('hostkey.changedHint')}</p>
      </Dialog>
    )
  }

  const passphrase = prompt.kind === 'passphrase'
  const submit = () => {
    const value = secret
    setSecret('')
    setRemember(false)
    answerPrompt(passphrase ? { value } : { value, store: remember })
  }
  const canStore = !passphrase && Boolean(prompt.data.canStore) && account?.vault === 'open'
  const hint = prompt.data.storedFailed ? t('password.storedFailed') : prompt.data.retry ? t('password.retry') : null

  return (
    <Dialog
      open
      title={passphrase ? t('password.passphraseTitle', { target: prompt.data.target ?? session.name }) : t('password.title', { target: prompt.data.target ?? session.name })}
      onClose={() => answerPrompt(null)}
      footer={
        <>
          <Button variant="ghost" onClick={() => answerPrompt(null)}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!secret} onClick={submit}>
            {t('password.connect')}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (secret) submit()
        }}
        className="flex flex-col gap-4"
      >
        {hint && <Banner tone="warn">{hint}</Banner>}
        <Field label={passphrase ? t('password.passphrase') : t('password.label')} type="password" value={secret} onChange={(event) => setSecret(event.target.value)} autoFocus autoComplete="off" />
        {canStore && <Switch label={t('password.remember')} hint={t('password.rememberHint')} checked={remember} onChange={setRemember} />}
      </form>
    </Dialog>
  )
}
