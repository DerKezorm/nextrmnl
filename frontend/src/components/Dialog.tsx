import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { Symbol } from './Symbol'

/** The open dialogs, topmost last. Escape only closes the topmost one. */
const openDialogs: object[] = []

/** Dialog like in nexcrate. Escape and a click outside it close it, the cross is the visible way out. */
export function Dialog({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  const { t } = useTranslation()
  const panelRef = useRef<HTMLDivElement>(null)
  // Not as a dependency: otherwise the effect would run on every render and pull focus out of the field.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    const token = {}
    openDialogs.push(token)
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && openDialogs[openDialogs.length - 1] === token) onCloseRef.current()
    }
    document.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    if (!panelRef.current?.contains(document.activeElement)) panelRef.current?.focus()
    return () => {
      const index = openDialogs.indexOf(token)
      if (index >= 0) openDialogs.splice(index, 1)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [open])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 shadow-2xl shadow-black/60 outline-none"
      >
        <div className="flex items-center justify-between gap-4 border-b border-ink-700 px-6 py-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label={t('common.close')} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100">
            <Symbol name="close" />
          </button>
        </div>
        <div className="flex flex-col gap-4 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-ink-700 px-6 py-4">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}
