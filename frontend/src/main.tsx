import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import { AuthProvider } from './auth'
import { NoticeProvider } from './components/Notice'
import { startI18n } from './i18n'
import './styles/index.css'

function startApp(): void {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <BrowserRouter>
        <NoticeProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </NoticeProvider>
      </BrowserRouter>
    </StrictMode>,
  )
}

function startFailed(): void {
  const root = document.getElementById('root')
  if (root) {
    root.innerHTML = '<p style="font-family:system-ui;padding:2rem;color:#c3c3ce">nextrmnl could not load its texts. Reload the page.</p>'
  }
}

// Texts first, then the UI. Otherwise the raw key list would show briefly.
startI18n().then(startApp, startFailed)
