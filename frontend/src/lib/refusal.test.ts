import { describe, expect, it } from 'vitest'
import en from '../i18n/en.json'
import { refusalDetail } from './refusal'

describe('refusalDetail', () => {
  it('names each refusal of the server', () => {
    expect(refusalDetail(4400, true)).toBe('bad_target')
    expect(refusalDetail(4401, true)).toBe('not_signed_in')
    expect(refusalDetail(4403, true)).toBe('foreign_origin')
    expect(refusalDetail(4404, true)).toBe('connection_not_found')
    expect(refusalDetail(4429, true)).toBe('too_many_sessions')
  })

  it('tells a socket that never opened from one that dropped', () => {
    expect(refusalDetail(1006, false)).toBe('socket_failed')
    expect(refusalDetail(1006, true)).toBe('connection_lost')
  })

  it('has a sentence for every reason', () => {
    const fail = en.terminal.fail as Record<string, string>
    for (const code of [4400, 4401, 4403, 4404, 4429, 1006]) {
      for (const opened of [false, true]) expect(fail[refusalDetail(code, opened)]).toBeTruthy()
    }
  })
})
