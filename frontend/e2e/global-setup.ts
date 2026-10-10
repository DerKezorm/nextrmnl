/**
 * Before any test: the operator account, made through the first setup, and its session saved for every test
 * (`storageState`). Name and password are made up for the run and live only in its data directory.
 */
import { request, type FullConfig } from '@playwright/test'

export const OPERATOR = { name: 'tester', password: 'e2e test operator password' }
export const UI = { 'X-Requested-By': 'nextrmnl' }

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0].use.baseURL!
  const context = await request.newContext({ baseURL, extraHTTPHeaders: UI })
  const made = await context.post('/api/setup', { data: OPERATOR })
  if (!made.ok()) throw new Error(`setup failed: ${made.status()} ${await made.text()}`)
  await context.storageState({ path: process.env.NEXTRMNL_E2E_STATE! })
  await context.dispose()
}
