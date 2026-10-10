/**
 * End-to-end, headless. The built frontend is served by the real backend, as in the container. Own data directory
 * and own ports (backend 8471, stand-in authentik 8477), so a running development server is never measured.
 *
 *   npm run e2e        build, start the backend and the stand-in authentik, test
 *
 * The stand-in's API token is made per run here; the workers load this file again and inherit it through the
 * environment.
 */
import { defineConfig } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const PORT = 8471
const AUTHENTIK_PORT = 8477

function made(name: string, value: () => string): string {
  process.env[name] ??= value()
  return process.env[name]!
}

const DATA_DIR = made('NEXTRMNL_E2E_DATA', () => fs.mkdtempSync(path.join(os.tmpdir(), 'nextrmnl-e2e-')))
made('NEXTRMNL_E2E_STATE', () => path.join(DATA_DIR, 'operator-state.json'))
const AUTHENTIK_TOKEN = made('E2E_AUTHENTIK_TOKEN', () => crypto.randomBytes(24).toString('base64url'))
const python = process.platform === 'win32' ? path.join('.venv', 'Scripts', 'python.exe') : path.join('.venv', 'bin', 'python')

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
    storageState: process.env.NEXTRMNL_E2E_STATE,
    locale: 'en-US',
  },
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT}`,
      cwd: path.join('..', 'backend'),
      url: `http://127.0.0.1:${PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        NEXTRMNL_DATA_DIR: DATA_DIR,
        NEXTRMNL_FRONTEND_DIST: path.resolve('dist'),
        NEXTRMNL_DISABLE_BACKGROUND: '1',
        NEXTRMNL_ARGON2_TIME: '1',
        NEXTRMNL_ARGON2_MEMORY_KIB: '8192',
        NEXTRMNL_ARGON2_PARALLELISM: '1',
        NEXTRMNL_COOKIE_SECURE: 'off',
      },
    },
    {
      command: 'node e2e/fake-authentik.mjs',
      url: `http://127.0.0.1:${AUTHENTIK_PORT}/health`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: { FAKE_AK_PORT: String(AUTHENTIK_PORT), FAKE_AK_TOKEN: AUTHENTIK_TOKEN },
    },
  ],
})
