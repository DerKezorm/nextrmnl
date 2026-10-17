import { beforeAll, describe, expect, it } from 'vitest'

import { startI18n } from '../i18n'
import { errorText, translateError } from './client'

beforeAll(async () => {
  await startI18n('en')
})

describe('error sentences', () => {
  it('come from nextrmnl first, then from the shared sign-in module', () => {
    // nextrmnl's own sentence for a code the module names says what the module says.
    expect(errorText('invite_invalid')).toBe('The invitation ran out or has been used.')
    // A code only the module knows: its sentence, never the bare code.
    expect(errorText('oidc_subject_taken')).toBe('This identity is linked to another account already.')
    expect(translateError({ code: 'slug_taken', message: 'x' }, 422)).toBe('Another entry has this short name already.')
  })

  it('leave an unknown code as it is', () => {
    expect(errorText('no_such_code_anywhere')).toBe('no_such_code_anywhere')
  })
})
