import { useTranslation } from 'react-i18next'
import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuth } from './auth'
import { AppShell } from './components/AppShell'
import { Banner, Button, PageLoading } from './components/ui'
import { AboutPage } from './pages/AboutPage'
import { InvitePage } from './pages/auth/InvitePage'
import { LoginPage } from './pages/auth/LoginPage'
import { SetupPage } from './pages/auth/SetupPage'
import { SessionsPage } from './pages/SessionsPage'
import { SettingsPage } from './pages/SettingsPage'
import { VaultPage } from './pages/VaultPage'
import { WorkspacePage } from './pages/WorkspacePage'
import { WorkspaceProvider } from './state/workspace'

function Unreachable() {
  const { t } = useTranslation()
  const { refresh } = useAuth()
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6">
      <Banner tone="bad">{t('errors.network')}</Banner>
      <Button variant="ghost" onClick={() => void refresh()}>
        {t('common.retry')}
      </Button>
    </div>
  )
}

/** Before sign-in there are only three pages; after that, the shell with everything. */
export default function App() {
  const { state } = useAuth()

  if (state === 'loading') return <PageLoading />
  if (state === 'unreachable') return <Unreachable />
  if (state === 'setup') return <SetupPage />
  if (state === 'signed_out') {
    return (
      <Routes>
        <Route path="/invite/:token" element={<InvitePage />} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    )
  }

  return (
    <WorkspaceProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<WorkspacePage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="vault" element={<VaultPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="about" element={<AboutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </WorkspaceProvider>
  )
}
