/**
 * Pflichttests 33 and 34 of the shared sign-in blueprint (copied once from bauplaene/oidc, then nextrmnl's file).
 *
 * The provider is the stand-in authentik of e2e/fake-authentik.mjs (its API for the authentik button, and an OpenID
 * Connect provider over http on 8477 with a key made at its start, sending the browser straight back with a code for
 * whoever POST /next named). Never a real provider, never the network. The page is the operator of global-setup.ts.
 */
import { expect, test, type Page } from '@playwright/test'

import { OPERATOR, UI } from './global-setup'

const AUTHENTIK = 'http://127.0.0.1:8477'

function collectProblems(page: Page): string[] {
  const problems: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error' && !/status of 4(01|04|09|22)/.test(message.text())) problems.push(message.text())
  })
  page.on('pageerror', (error) => problems.push(error.message))
  return problems
}

async function person(sub: string, name: string): Promise<void> {
  await fetch(`${AUTHENTIK}/next`, { method: 'POST', body: JSON.stringify({ sub, preferred_username: name, email: `${name}@example.com` }) })
}

test('33: sign-in through the provider button ends in a session', async ({ page, browser }) => {
  const problems = collectProblems(page)
  // The authentik button makes provider, application and the entry "authentik" at the stand-in.
  await page.goto('/settings?tab=signin')
  await page.getByLabel('Address of authentik').fill(AUTHENTIK)
  await page.getByLabel('API token').fill(process.env.E2E_AUTHENTIK_TOKEN!)
  await page.getByRole('button', { name: 'Set up', exact: true }).click()
  await expect(page.getByTestId('authentik-steps')).toContainText('Settings here')
  await expect(page.getByText('set up through authentik')).toBeVisible()
  // The operator links the own account through its page: the password once more, off to the provider and back.
  await person('tester-33', 'someone-else')
  await page.goto('/settings?tab=account')
  const mine = page.getByTestId('my-providers')
  await mine.getByRole('button', { name: 'Link', exact: true }).click()
  await page.getByRole('dialog').getByLabel('Your password').fill(OPERATOR.password)
  await page.getByRole('dialog').getByRole('button', { name: 'Link', exact: true }).click()
  await expect(page.getByText('Linked to authentik.')).toBeVisible()
  await expect(mine).toContainText('Linked')
  // A browser without a session: the button on the sign-in page, and the session is the operator's.
  const fresh = await browser.newContext({ storageState: { cookies: [], origins: [] }, locale: 'en-US' })
  const other = await fresh.newPage()
  const otherProblems = collectProblems(other)
  await other.goto('/connect/7')
  await expect(other.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible()
  await other.getByRole('button', { name: 'Sign in with authentik' }).click()
  // The direct link survives the way through the provider.
  await other.waitForURL((url) => url.pathname === '/connect/7')
  await expect(other).not.toHaveURL(/error=/)
  expect((await (await other.request.get('/api/auth/me')).json()).name).toBe(OPERATOR.name)
  // Somebody the provider knows and nextrmnl does not: refused with the blueprint's sentence.
  await person('stranger-33', 'stranger')
  const strangerContext = await browser.newContext({ storageState: { cookies: [], origins: [] }, locale: 'en-US' })
  const stranger = await strangerContext.newPage()
  await stranger.goto('/login')
  await stranger.getByRole('button', { name: 'Sign in with authentik' }).click()
  await stranger.waitForURL(/error=oidc_no_account/)
  await expect(stranger.getByText('There is no account for you here.', { exact: false })).toBeVisible()
  await fresh.close()
  await strangerContext.close()
  expect([...problems, ...otherProblems]).toEqual([])
})

test('34: add, edit and remove a provider; the authentik card lists its steps', async ({ page }) => {
  const problems = collectProblems(page)
  await page.goto('/settings?tab=signin')
  // An issuer the stand-in answers for: the application the button made in test 33, or made here.
  const listed = (await (await page.request.get('/api/oidc/admin/providers')).json()) as { issuer: string; managed: string }[]
  let issuer = listed.find((entry) => entry.managed === 'authentik')?.issuer
  if (!issuer) {
    const answer = await page.request.post('/api/oidc/authentik/setup', { data: { url: AUTHENTIK, token: process.env.E2E_AUTHENTIK_TOKEN }, headers: UI })
    issuer = ((await answer.json()) as { issuer: string }).issuer
    await page.reload()
  }
  await page.getByRole('button', { name: 'Add a provider' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Name on the button').fill('Stand-in')
  await expect(dialog.getByLabel('Short name')).toHaveValue('stand-in')
  await dialog.getByLabel('Issuer', { exact: true }).fill(issuer)
  await dialog.getByLabel('Client ID').fill('e2e-client')
  await dialog.getByLabel('Client secret').fill(`e2e-${Date.now()}`)
  await expect(dialog.getByText('/api/oidc/stand-in/callback')).toBeVisible()
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  const list = page.getByTestId('provider-list')
  const row = list.locator('li', { hasText: 'Stand-in' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Edit' }).click()
  await dialog.getByLabel('Name on the button').fill('Stand-in 2')
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(list.locator('li', { hasText: 'Stand-in 2' })).toBeVisible()
  // The sign-in page has a button for each active provider, in the order of the list.
  const buttons = await page.request.get('/api/auth/methods')
  expect(((await buttons.json()) as { providers: { label: string }[] }).providers.map((entry) => entry.label)).toEqual(['authentik', 'Stand-in 2'])
  await list.locator('li', { hasText: 'Stand-in 2' }).getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByRole('dialog')).toContainText('accounts lose the link')
  await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click()
  await expect(list.locator('li', { hasText: 'Stand-in 2' })).toHaveCount(0)
  // The authentik card: an address nobody answers at gives the step list with the first step failed.
  await page.getByLabel('Address of authentik').fill('http://127.0.0.1:9')
  await page.getByLabel('API token').fill(`e2e-${Date.now()}`)
  await page.getByRole('button', { name: 'Set up', exact: true }).click()
  await expect(page.getByText('Connection to authentik')).toBeVisible()
  await expect(page.getByText('cannot be reached at this address', { exact: false })).toBeVisible()
  expect(problems).toEqual([])
})
