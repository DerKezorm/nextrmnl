import { applyModifier } from './terminalCache'

describe('Ctrl and Alt from the on-screen key bar', () => {
  it('turns letters into their control characters, either case', () => {
    expect(applyModifier('ctrl', 'c')).toBe('\x03')
    expect(applyModifier('ctrl', 'C')).toBe('\x03')
    expect(applyModifier('ctrl', 'd')).toBe('\x04')
    expect(applyModifier('ctrl', '[')).toBe('\x1b')
    expect(applyModifier('ctrl', ' ')).toBe('\x00')
    expect(applyModifier('ctrl', '?')).toBe('\x7f')
  })

  it('leaves what has no control form alone', () => {
    expect(applyModifier('ctrl', '1')).toBe('1')
    expect(applyModifier('ctrl', 'ab')).toBe('ab')
  })

  it('puts Escape in front for Alt, as terminals do', () => {
    expect(applyModifier('alt', 'b')).toBe('\x1bb')
    expect(applyModifier('alt', '.')).toBe('\x1b.')
  })
})
