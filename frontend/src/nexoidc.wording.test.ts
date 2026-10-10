/**
 * Pflichttest 35 (Bauplan 05): every text of the oidc module is in the app, in German and English, through the app's
 * own i18n. Copied once by `bauplaene/oidc/tools/sync.py` to `frontend/src/nexoidc.wording.test.ts`; then the
 * app's file. Fill in the places marked APP.
 *
 * The module's own tests already check the wording files against Bauplan 04 word for word; this test checks that the
 * app really loads them (merged into its resources, `{{app}}` filled in) and that nothing in the app overrides them.
 */
import { describe, expect, it } from 'vitest'

import { oidcWording, type OidcWording } from './vendor/nexoidc/oidc'
import de from './vendor/nexoidc/oidc.de.json'
import en from './vendor/nexoidc/oidc.en.json'
// The app's i18n instance after its own set-up (startI18n merges the module's files into the resources).
import i18n, { changeLanguage, startI18n } from './i18n'

const APP_NAME = 'nextrmnl'
// nextrmnl has several accounts: the app merges the files through oidcWording(…, { singleAccount: false }).
const SINGLE_ACCOUNT = false

type Tree = OidcWording

function leaves(tree: Tree, prefix = ''): [string, string][] {
  return Object.entries(tree).flatMap(([name, value]) => {
    const key = prefix ? `${prefix}.${name}` : name
    return typeof value === 'string' ? [[key, value] as [string, string]] : leaves(value, key)
  })
}

const values = { name: 'N', count: 2, only: 1, status: 403 }

describe('the oidc texts', () => {
  for (const [language, tree] of [['de', de], ['en', en]] as const) {
    it(`are all in the app in ${language}, word for word, with the app's name`, async () => {
      if (!i18n.isInitialized) await startI18n(language)
      await changeLanguage(language)
      for (const [key, text] of leaves(oidcWording(tree, { singleAccount: SINGLE_ACCOUNT }))) {
        // Plural forms are asked for without their suffix, with a count that picks them (since 1.0.9).
        const plural = /_(one|other)$/.exec(key)
        const given = plural ? { ...values, count: plural[1] === 'one' ? 1 : 2 } : values
        const asked = plural ? key.slice(0, plural.index) : key
        const expected = text.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
          name === 'app' ? APP_NAME : String(given[name as keyof typeof given]),
        )
        expect(i18n.t(asked, given), key).toBe(expected)
      }
    })
  }
})

describe('the oidc texts of an app with several accounts', () => {
  it('leave out the sentences for one account, and pick the plural form by the count', async () => {
    if (!i18n.isInitialized) await startI18n('en')
    await changeLanguage('en')
    // oidcWording hands on `oidc.single` only to the apps with one account (nexcrate, nexsift).
    expect(i18n.exists('oidc.single.admin.passwordHint')).toBe(false)
    expect(i18n.exists('oidc.admin.passwordHint')).toBe(true)
    expect(i18n.t('oidc.admin.issuerChangeConfirm', { count: 1 })).not.toBe(i18n.t('oidc.admin.issuerChangeConfirm', { count: 2 }))
  })
})
