import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { api } from '../../api/client'
import { useAuth } from '../../auth'
import { Banner, PageLoading } from '../../components/ui'
import { useLoad } from '../../lib/useLoad'
import { AuthFrame } from './AuthFrame'
import { AccountForm } from './SetupPage'

/** The link from an invite. The token only lives in the address, never in the page. */
export function InvitePage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const { acceptInvite } = useAuth()
  const invite = useLoad(() => api.get<{ name: string; role: string }>(`/api/invites/${encodeURIComponent(token)}`), [token])

  return (
    <AuthFrame wide>
      {invite.loading ? (
        <PageLoading />
      ) : invite.error || !invite.data ? (
        <div className="flex flex-col gap-4">
          <Banner tone="bad">{t('auth.inviteInvalid')}</Banner>
          <Link to="/login" className="text-sm text-accent-400 hover:underline">
            {t('auth.toSignIn')}
          </Link>
        </div>
      ) : (
        <AccountForm
          title={t('auth.inviteTitle')}
          lead={t('auth.inviteLead', { role: t(`settings.role.${invite.data.role}`) })}
          submitLabel={t('auth.inviteSubmit')}
          initialName={invite.data.name}
          onSubmit={(name, password) => acceptInvite(token, name, password)}
        />
      )}
    </AuthFrame>
  )
}
