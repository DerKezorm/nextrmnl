import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { armModifier, arrowKey, MODIFIER_EVENT, typeInto, type ModifierDetail } from '../../lib/terminalCache'

/** Phones and tablets: a finger, not a mouse. A laptop with a touch screen keeps its keyboard and gets no bar. */
function useTouch(): boolean {
  const query = '(pointer: coarse) and (hover: none)'
  const [touch, setTouch] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setTouch(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return touch
}

type Key = { label: string; name?: string; send?: string; arrow?: 'up' | 'down' | 'left' | 'right'; modifier?: 'ctrl' | 'alt' }

const KEYS: Key[] = [
  { label: 'Esc', send: '\x1b' },
  { label: 'Tab', send: '\t' },
  { label: 'Strg', name: 'ctrl', modifier: 'ctrl' },
  { label: 'Alt', modifier: 'alt' },
  { label: '←', name: 'left', arrow: 'left' },
  { label: '↓', name: 'down', arrow: 'down' },
  { label: '↑', name: 'up', arrow: 'up' },
  { label: '→', name: 'right', arrow: 'right' },
  { label: '|', send: '|' },
  { label: '~', send: '~' },
  { label: '/', send: '/' },
  { label: '-', send: '-' },
]

/**
 * The keys a phone keyboard lacks, above it. Ctrl and Alt hold for the next key (Ctrl, then C is Ctrl+C) and
 * light up meanwhile. The buttons never take the focus, so the phone keyboard stays open.
 */
export function MobileKeys({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation()
  const touch = useTouch()
  const [armed, setArmed] = useState<'ctrl' | 'alt' | null>(null)

  useEffect(() => {
    const onModifier = (event: Event) => {
      const detail = (event as CustomEvent<ModifierDetail>).detail
      if (detail.sessionId === sessionId) setArmed(detail.modifier)
    }
    window.addEventListener(MODIFIER_EVENT, onModifier)
    return () => window.removeEventListener(MODIFIER_EVENT, onModifier)
  }, [sessionId])

  if (!touch) return null

  const press = (key: Key) => {
    if (key.modifier) armModifier(sessionId, key.modifier)
    else if (key.arrow) arrowKey(sessionId, key.arrow)
    else if (key.send) typeInto(sessionId, key.send)
  }

  return (
    <div role="toolbar" aria-label={t('mobileKeys.label')} className="nt-scroll flex shrink-0 gap-1 overflow-x-auto border-t border-ink-700 bg-ink-900 px-2 py-1.5">
      {KEYS.map((key) => {
        const on = key.modifier !== undefined && armed === key.modifier
        return (
          <button
            key={key.label}
            type="button"
            tabIndex={-1}
            aria-pressed={key.modifier ? on : undefined}
            aria-label={key.name ? t(`mobileKeys.${key.name}`) : undefined}
            // Keeps the focus (and the phone keyboard) in the terminal.
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => press(key)}
            className={
              'min-w-[2.6rem] shrink-0 rounded-lg px-2.5 py-2 font-mono text-sm font-medium select-none ' +
              (on ? 'bg-accent-500 text-on-accent' : 'bg-ink-800 text-mist-200 active:bg-ink-700')
            }
          >
            {key.label === 'Strg' ? t('mobileKeys.ctrlShort') : key.label}
          </button>
        )
      })}
    </div>
  )
}
