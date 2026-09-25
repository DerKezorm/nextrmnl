import de from './de.json'

const sources = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

function exists(path: string): boolean {
  let node: unknown = de
  for (const part of path.split('.')) {
    if (!node || typeof node !== 'object' || !(part in node)) return false
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'string'
}

/** With plural forms: `list.footer` exists as `_one` and `_other`. */
function existsWithPlural(path: string): boolean {
  return exists(path) || (exists(`${path}_one`) && exists(`${path}_other`))
}

describe('translation keys used in the code', () => {
  it('all exist', () => {
    const used = new Set<string>()
    for (const [file, text] of Object.entries(sources)) {
      if (file.endsWith('.test.ts') || file.endsWith('.test.tsx')) continue
      for (const match of text.matchAll(/\bt\(\s*'([a-zA-Z0-9_.]+)'/g)) used.add(match[1])
    }
    // Floor check, so a broken pattern does not silently find nothing.
    expect(used.size).toBeGreaterThan(200)
    expect([...used].filter((key) => !existsWithPlural(key))).toEqual([])
  })

  it('cover the composed keys', () => {
    const composed = [
      ...['up', 'down', 'unknown'].map((reach) => `reach.${reach}`),
      ...['running', 'normal', 'failed', 'hostkey'].map((end) => `sessions.end.${end}`),
      ...['operator', 'member'].map((role) => `settings.role.${role}`),
      ...['reached', 'signingKey', 'mapping', 'provider', 'application', 'filled'].map((step) => `signin.step.${step}`),
      ...['nexview', 'nexmail', 'nexdeck', 'nexcrate', 'nexbeat', 'nexpulse'].map((app) => `about.app.${app}`),
      ...['off', 'daily', 'weekly', 'monthly'].map((schedule) => `backups.schedule.${schedule}`),
      ...['auto', 'update', 'manual'].map((kind) => `backups.kind.${kind}`),
      ...['quiet', 'normal', 'detailed', 'trace'].flatMap((mode) => [`logs.mode.${mode}`, `logs.modeDesc.${mode}`]),
      ...['0', '30', '120', '480'].map((minutes) => `logs.duration.${minutes}`),
      ...['terminal', 'secrets', 'files'].map((item) => `logs.never.${item}`),
      'sessions.end.cut',
      'edit.sharedReadOnly',
      'edit.sharedOwnAccess',
      ...['backups', 'restore'].flatMap((area) => [`${area}.tooNew`, `${area}.unknownVersion`]),
      // The failure reasons the server names while connecting; `terminal.fail.other` catches the rest.
      ...['target_not_allowed', 'vault_locked', 'timeout', 'unreachable', 'auth_failed', 'not_signed_in', 'connection_lost', 'disconnected_by_operator', 'other'].map((detail) => `terminal.fail.${detail}`),
      // The server's error codes, which the UI translates into sentences.
      ...['not_signed_in', 'wrong_credentials', 'account_locked', 'vault_locked', 'password_too_short', 'target_not_allowed', 'oidc_denied', 'oidc_token_invalid'].map((code) => `errors.byCode.${code}`),
    ]
    expect(composed.filter((key) => !exists(key))).toEqual([])
  })
})
