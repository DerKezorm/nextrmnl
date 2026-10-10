import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../auth', () => ({
  useAuth: () => ({ signIn: vi.fn(), signInCode: vi.fn(), signInPasskey: vi.fn(), cancelSecondFactor: vi.fn() }),
}))
// Two providers, password sign-in as the server says in each test.
const methods = { password: true, providers: [{ slug: 'sso', label: 'authentik' }, { slug: 'work', label: 'Entra' }] }
const pending = { name: 'anna', methods: ['totp'] }
vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: {
    get: vi.fn(async (path: string) => (path === '/api/auth/login/pending' ? pending : methods)),
    post: vi.fn(),
  },
}))

import { LoginPage } from './LoginPage'

afterEach(() => {
  cleanup()
  methods.password = true
})

function at(address: string) {
  render(
    <MemoryRouter initialEntries={[address]}>
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** Signed out, every address shows the sign-in page (App.tsx); the start address of each provider's button. */
async function startsAt(address: string): Promise<string[]> {
  at(address)
  let buttons: HTMLButtonElement[] = []
  await waitFor(() => {
    buttons = [...document.querySelectorAll<HTMLButtonElement>('button[data-start]')]
    expect(buttons).toHaveLength(2)
  })
  return buttons.map((button) => button.dataset.start ?? '')
}

describe('LoginPage, the provider buttons', () => {
  it('show one button per provider in the order of the list, and take a direct link along', async () => {
    expect(await startsAt('/connect/12')).toEqual(['/api/oidc/sso/start?next=%2Fconnect%2F12', '/api/oidc/work/start?next=%2Fconnect%2F12'])
  })

  it('take nothing along from the sign-in page itself', async () => {
    expect(await startsAt('/login?error=oidc_no_account')).toEqual(['/api/oidc/sso/start', '/api/oidc/work/start'])
  })

  it('stand alone when password sign-in is off, with the operator way below', async () => {
    methods.password = false
    await startsAt('/')
    expect(document.querySelector('input[type="password"]')).toBeNull()
    screen.getByRole('button', { name: /oidc\.login\.operator|operator/ }).click()
    await waitFor(() => expect(document.querySelector('input[type="password"]')).not.toBeNull())
  })

  it('never show a raw code from the address', async () => {
    at('/login?error=%3Cb%3Ehello%3C%2Fb%3E')
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('alert').textContent).not.toContain('hello')
  })

  it('ask for the code of the second factor after a provider that leaves it to nextrmnl', async () => {
    at('/login?step=code')
    await waitFor(() => expect(document.querySelector('input[autocomplete="one-time-code"]')).not.toBeNull())
  })
})
