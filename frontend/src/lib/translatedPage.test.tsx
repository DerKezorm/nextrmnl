import { render, screen } from '@testing-library/react'

import { bearTranslatedPages } from './translatedPage'

/** Does what Chrome's translator does: every text node becomes <font><font>…</font></font>. */
function translateLikeChrome(root: Node) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const texts: Text[] = []
  while (walker.nextNode()) texts.push(walker.currentNode as Text)
  for (const text of texts) {
    const outer = document.createElement('font')
    const inner = document.createElement('font')
    inner.textContent = text.data
    outer.appendChild(inner)
    text.replaceWith(outer)
  }
}

function Line({ busy, note }: { busy: boolean; note: boolean }) {
  return (
    <p data-testid="line">
      {busy && <b>busy</b>}
      Saved
      {note && ' just now'}
    </p>
  )
}

beforeAll(() => {
  bearTranslatedPages()
})

// After Chrome translated the page, React put an element before a text node Chrome had taken away.
test('a translated page keeps working when something appears before a text', () => {
  const { rerender } = render(<Line busy={false} note={false} />)
  translateLikeChrome(screen.getByTestId('line'))

  rerender(<Line busy note={false} />)

  // Where the text belonged nobody knows any more; the new part stands at the end instead of not at all.
  expect(screen.getByTestId('line').querySelector('b')).not.toBeNull()
})

test('something meant to go before a wrapped node goes before its wrapper', () => {
  const host = document.createElement('p')
  const text = document.createTextNode('Saved')
  host.append(document.createTextNode('a'), text)
  const wrapper = document.createElement('font')
  host.replaceChild(wrapper, text)
  wrapper.appendChild(text)

  host.insertBefore(document.createElement('b'), text)

  expect([...host.childNodes].map((node) => node.nodeName)).toEqual(['#text', 'B', 'FONT'])
})

test('a translated page keeps working when a text goes away', () => {
  const { rerender } = render(<Line busy={false} note />)
  translateLikeChrome(screen.getByTestId('line'))

  rerender(<Line busy={false} note={false} />)

  expect(screen.getByTestId('line').isConnected).toBe(true)
})

test('an untouched page still puts things where React wants them', () => {
  const { rerender } = render(<Line busy={false} note={false} />)
  rerender(<Line busy note />)
  expect(screen.getByTestId('line').textContent).toBe('busySaved just now')

  rerender(<Line busy={false} note={false} />)
  expect(screen.getByTestId('line').textContent).toBe('Saved')
})
