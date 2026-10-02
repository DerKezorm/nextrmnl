import { cleanup, render, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../auth', () => ({
  useAuth: () => ({ signIn: vi.fn(), signInCode: vi.fn(), signInPasskey: vi.fn(), cancelSecondFactor: vi.fn() }),
}))
// The provider is set up: the page shows its button.
vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: { get: vi.fn(async () => ({ enabled: true, provider_name: 'authentik' })), post: vi.fn() },
}))

import { LoginPage } from './LoginPage'

afterEach(cleanup)

/** Signed out, every address shows the sign-in page (App.tsx); this is that, at one address. */
async function providerButtonAt(address: string): Promise<string | null> {
  render(
    <MemoryRouter initialEntries={[address]}>
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    </MemoryRouter>,
  )
  let button: HTMLAnchorElement | null = null
  await waitFor(() => {
    button = document.querySelector<HTMLAnchorElement>('a[href^="/api/oidc/start"]')
    expect(button).not.toBeNull()
  })
  return (button as HTMLAnchorElement | null)?.getAttribute('href') ?? null
}

describe('LoginPage, the provider button', () => {
  it('takes a direct link along, so the way back lands on it', async () => {
    expect(await providerButtonAt('/connect/12')).toBe('/api/oidc/start?next=%2Fconnect%2F12')
  })

  it('takes nothing along from the sign-in page itself', async () => {
    expect(await providerButtonAt('/login?error=oidc_denied')).toBe('/api/oidc/start')
  })
})
