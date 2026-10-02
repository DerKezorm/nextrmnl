import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * A panel below a button, attached to `body` like the row menu of the connection list: the terminal card clips
 * anything that reaches past its edge. A click outside, Escape, scrolling or resizing close it. The button that
 * opens it carries `data-popover-toggle` and closes it itself.
 */
export function Popover({
  anchor,
  width,
  onClose,
  children,
  label,
}: {
  anchor: DOMRect
  width: number
  onClose: () => void
  children: ReactNode
  label: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const close = () => onCloseRef.current()
    function onPointer(event: PointerEvent) {
      const target = event.target as HTMLElement
      if (ref.current?.contains(target) || target.closest('[data-popover-toggle]')) return
      close()
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') close()
    }
    function onScroll(event: Event) {
      // Scrolling inside the panel (a long list) is not a reason to close it.
      if (event.target instanceof Node && ref.current?.contains(event.target)) return
      close()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', close)
    }
  }, [])

  const left = Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8))
  const style = { top: anchor.bottom + 6, left, width, maxHeight: Math.max(160, window.innerHeight - anchor.bottom - 24) }
  return createPortal(
    <div ref={ref} role="dialog" aria-label={label} style={style} className="fixed z-50 flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850 shadow-2xl shadow-black/50">
      {children}
    </div>,
    document.body,
  )
}
