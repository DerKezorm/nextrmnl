import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { changeLanguage, startI18n } from '../../i18n'
import de from '../../vendor/nexoidc/oidc.de.json'
import en from '../../vendor/nexoidc/oidc.en.json'

vi.mock('../../components/Notice', () => ({ useNotice: () => vi.fn() }))

const settings = { password_login: true, two_factor_required: false, public_url: '' }
let address = {
  public_url: '',
  seen: 'http://localhost:3000',
  forwarded_proto: '',
  forwarded_host: '',
  proxy_ignored: false,
  callback_path: '/api/oidc/authentik/callback',
  redirect_uri: 'http://localhost:3000/api/oidc/authentik/callback',
  registered_redirect_uri: '',
  stale: false,
}
const get = vi.fn()
const put = vi.fn()
const post = vi.fn()
vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: { get: (...args: unknown[]) => get(...args), put: (...args: unknown[]) => put(...args), post: (...args: unknown[]) => post(...args) },
}))

import { ApiError } from '../../api/client'
import { SignInCards } from './SignInCards'

beforeEach(async () => {
  await startI18n('en')
  await changeLanguage('en')
  address = { ...address, public_url: '', seen: 'http://localhost:3000', proxy_ignored: false, registered_redirect_uri: '', stale: false }
  get.mockImplementation(async (path: string) => {
    if (path === '/api/settings') return { ...settings }
    if (path === '/api/oidc/admin/providers') return []
    if (path === '/api/oidc/authentik/address') return { ...address }
    throw new Error(`unexpected GET ${path}`)
  })
})

afterEach(() => {
  cleanup()
  get.mockReset()
  put.mockReset()
  post.mockReset()
})

const question = (count: number, hand: number) =>
  new ApiError(409, 'asks', 'public_url_change', { code: 'public_url_change', count, hand, address: 'https://old.example.com/api/oidc/authentik/callback' })

describe('the sign-in cards: the second factor requirement', () => {
  for (const [language, trusts] of [
    ['de', de.oidc.admin.trustsSecondFactor],
    ['en', en.oidc.admin.trustsSecondFactor],
  ] as const) {
    it(`names the switch of the provider entry that exempts a sign-in (${language})`, async () => {
      await changeLanguage(language)
      const { container } = render(<SignInCards />)
      await screen.findByText(language === 'de' ? /Nicht betroffen ist/ : /Not affected is/)
      // The words of the switch itself, between the quotes of the sentence: not a gap.
      const open = language === 'de' ? '„' : '“'
      const close = language === 'de' ? '“' : '”'
      expect(container.textContent).toContain(`${open}${trusts}${close}`)
    })
  }
})

describe('the sign-in cards: the public address', () => {
  it('suggests the address of the browser while there is none, and sends it with the button', async () => {
    post.mockResolvedValue({ ok: true, steps: [], client_id: '', issuer: '', provider_id: null, links_dropped: 0 })
    render(<SignInCards />)
    const proposal = await screen.findByTestId('authentik-proposal')
    expect(proposal.textContent).toBe(window.location.origin)
    fireEvent.change(screen.getByLabelText('Address of authentik'), { target: { value: 'https://auth.example.com' } })
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'tok' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use it and set up' }))
    await waitFor(() => expect(post).toHaveBeenCalled())
    expect(post.mock.calls[0]).toEqual([
      '/api/oidc/authentik/setup',
      { url: 'https://auth.example.com', token: 'tok', public_url: window.location.origin },
    ])
  })

  it('warns when the server sees another scheme than the browser, and not when they agree', async () => {
    address.seen = window.location.origin.replace(/^http:/, 'https:')
    const { unmount } = render(<SignInCards />)
    expect((await screen.findByTestId('authentik-proxy-warning')).textContent).toContain(address.seen)
    unmount()
    address.seen = window.location.origin
    render(<SignInCards />)
    await screen.findByTestId('authentik-proposal')
    expect(screen.queryByTestId('authentik-proxy-warning')).toBeNull()
  })

  it('has the ordinary button and says what authentik still knows once the address is set and has moved on', async () => {
    address = { ...address, public_url: 'https://rmnl.example.com', registered_redirect_uri: 'http://old.example.com/api/oidc/authentik/callback', stale: true }
    render(<SignInCards />)
    expect((await screen.findByTestId('authentik-stale')).textContent).toContain('http://old.example.com/api/oidc/authentik/callback')
    expect(screen.queryByTestId('authentik-proposal')).toBeNull()
    expect(screen.getByRole('button', { name: 'Set up' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Use it only' })).toBeNull()
  })

  it('asks before the address of the settings changes what a provider knows, and saves only when confirmed', async () => {
    put.mockRejectedValueOnce(question(2, 1)).mockImplementation(async () => {
      address = { ...address, public_url: 'https://rmnl.example.com' }
      return { ...settings, public_url: 'https://rmnl.example.com' }
    })
    render(<SignInCards />)
    await screen.findByTestId('authentik-proposal')
    const field = await screen.findByPlaceholderText('https://nextrmnl.example.com')
    fireEvent.change(field, { target: { value: 'https://rmnl.example.com' } })
    fireEvent.submit(field.closest('form')!)
    const asked = await screen.findByTestId('address-question')
    expect(asked.textContent).toContain('https://old.example.com/api/oidc/authentik/callback')
    expect(asked.textContent).toContain('1 provider')
    expect(put.mock.calls[0]).toEqual(['/api/settings', { public_url: 'https://rmnl.example.com' }])
    fireEvent.click(screen.getByTestId('address-confirm'))
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    expect(put.mock.calls[1]).toEqual(['/api/settings', { public_url: 'https://rmnl.example.com', public_url_confirm: true }])
    // The authentik card reads what it shows again: the address is set now, so the suggestion goes.
    await waitFor(() => expect(screen.queryByTestId('authentik-proposal')).toBeNull())
  })

  it('asks the same before "Use it only" saves, with the entries by hand alone as their own sentence', async () => {
    post.mockRejectedValueOnce(question(0, 2)).mockResolvedValue({ ...address, public_url: window.location.origin })
    render(<SignInCards />)
    await screen.findByTestId('authentik-proposal')
    fireEvent.click(screen.getByRole('button', { name: 'Use it only' }))
    const asked = await screen.findByTestId('address-question')
    expect(asked.textContent).toContain('2 providers')
    expect(asked.textContent).not.toContain('still knows the redirect address')
    expect(post.mock.calls[0]).toEqual(['/api/oidc/authentik/address', { public_url: window.location.origin }])
    fireEvent.click(screen.getByTestId('address-confirm'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    expect(post.mock.calls[1]).toEqual(['/api/oidc/authentik/address', { public_url: window.location.origin, confirm: true }])
  })

  it('says so when the address cannot be used', async () => {
    post.mockRejectedValueOnce(new ApiError(422, 'bad', 'public_url_invalid', { code: 'public_url_invalid' }))
    render(<SignInCards />)
    await screen.findByTestId('authentik-proposal')
    fireEvent.click(screen.getByRole('button', { name: 'Use it only' }))
    expect((await screen.findByTestId('authentik-unusable')).textContent).toContain(en.oidc.authentik.why.unusable)
  })
})
