import { arrange, isLayout, place } from './panes'

describe('the fields of the split view', () => {
  it('fills a new layout with the active session first, then the tabs in order', () => {
    expect(arrange('grid', [], ['a', 'b', 'c'], 'b')).toEqual(['b', 'a', 'c', null])
    expect(arrange('columns', [], ['a', 'b', 'c'], 'c')).toEqual(['c', 'a'])
    expect(arrange('single', [], ['a', 'b'], 'b')).toEqual(['b'])
  })

  it('keeps what shows where it is and drops closed sessions', () => {
    expect(arrange('grid', ['c', 'a', null, 'gone'], ['a', 'b', 'c'], 'a')).toEqual(['c', 'a', 'b', null])
  })

  it('shows a session that became active in the focused field when nothing is free', () => {
    expect(arrange('columns', ['a', 'b'], ['a', 'b', 'c'], 'c', 1)).toEqual(['a', 'c'])
    expect(arrange('columns', ['a', 'b'], ['a', 'b', 'c'], 'c', 7)).toEqual(['a', 'c'])
  })

  it('never shows one session twice', () => {
    expect(arrange('grid', ['a', 'a', 'b', null], ['a', 'b'], 'a')).toEqual(['a', null, 'b', null])
  })

  it('swaps two fields when a session moves to where another shows', () => {
    expect(place(['a', 'b', 'c', null], 0, 'c')).toEqual(['c', 'b', 'a', null])
    expect(place(['a', null], 1, 'x')).toEqual(['a', 'x'])
    expect(place(['a', 'b'], 1, null)).toEqual(['a', null])
  })

  it('knows its layouts', () => {
    expect(isLayout('grid')).toBe(true)
    expect(isLayout('triple')).toBe(false)
  })
})
