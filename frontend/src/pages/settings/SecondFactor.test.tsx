import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Account } from '../../api/types'
import de from '../../vendor/nexoidc/oidc.de.json'
import en from '../../vendor/nexoidc/oidc.en.json'
import { changeLanguage, startI18n } from '../../i18n'

vi.mock('../../auth', () => ({ useAuth: () => ({ setAccount: vi.fn() }) }))
vi.mock('../../components/Notice', () => ({ useNotice: () => vi.fn() }))
vi.mock('./Passkeys', () => ({ PasskeysPart: () => null }))

import { SecondFactorSection } from './SecondFactor'

afterEach(cleanup)

const account = { name: 'anna', two_factor: false, two_factor_recovery_left: 0, second_factor_setup_required: false } as Account

describe('the second factor page of the own account', () => {
  for (const [language, switchText, promise] of [
    ['de', de.oidc.admin.trustsSecondFactor, /bringt dessen zweiten Faktor mit/],
    ['en', en.oidc.admin.trustsSecondFactor, /brings its second factor/],
  ] as const) {
    it(`does not promise a provider's second factor for every provider (${language}): only where the entry says so`, async () => {
      await startI18n(language)
      await changeLanguage(language)
      const { container } = render(<SecondFactorSection account={account} oidcOnly={false} provider="" />)
      const text = container.textContent ?? ''
      // The switch of the provider entry is named with the words of its own label, and the old blanket sentence is gone.
      expect(text).toContain(`${language === 'de' ? '„' : '“'}${switchText}${language === 'de' ? '“' : '”'}`)
      expect(text).not.toMatch(promise)
    })
  }
})
