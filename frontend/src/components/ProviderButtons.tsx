/**
 * The sign-in providers as buttons, in the order of the operator's list, with the line "or" above them: on the sign-in
 * page "Sign in with …", on the invitation page "Continue with …" (the invitation travels along to the provider).
 * Words from the shared sign-in module (`oidc.login.*`, `oidc.invite.button`), the same in every nex app.
 */
import { useTranslation } from 'react-i18next'

import type { OidcButton } from '../api/types'
import { oidcStartHref } from '../lib/oidcStart'
import { Symbol } from './Symbol'

export function OrLine() {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-3 text-xs text-mist-600">
      <span className="h-px flex-1 bg-ink-700" />
      {t('oidc.login.or')}
      <span className="h-px flex-1 bg-ink-700" />
    </div>
  )
}

export function ProviderButtons({
  providers,
  invite,
  next = '',
  withOr = true,
}: {
  providers: OidcButton[]
  /** The invitation's key: the buttons say "Continue with" and take it along. */
  invite?: string
  /** The page the person came for (a direct link), taken along to the provider and back. */
  next?: string
  /** The line "or" above, when something stands before the buttons. */
  withOr?: boolean
}) {
  const { t } = useTranslation()
  if (providers.length === 0) return null
  return (
    <>
      {withOr && <OrLine />}
      <div className="flex flex-col gap-2">
        {providers.map((provider) => (
          <button
            key={provider.slug}
            type="button"
            data-start={oidcStartHref(provider.slug, next, invite)}
            onClick={() => window.location.assign(oidcStartHref(provider.slug, next, invite))}
            className="inline-flex items-center justify-center gap-2 rounded-full border border-ink-700 bg-ink-850 px-5 py-2.5 text-sm font-semibold text-mist-300 hover:bg-ink-800 hover:text-mist-100"
          >
            <Symbol name="shield" />
            {invite ? t('oidc.invite.button', { name: provider.label }) : t('oidc.login.button', { name: provider.label })}
          </button>
        ))}
      </div>
    </>
  )
}
