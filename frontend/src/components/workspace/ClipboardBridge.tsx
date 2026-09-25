import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  CLIPBOARD_NOTE_EVENT,
  COPIED_EVENT,
  PASTE_CONFIRM_EVENT,
  pastedLines,
  peekTerminal,
  type ClipboardNoteDetail,
  type CopiedDetail,
  type PasteConfirmDetail,
} from '../../lib/terminalCache'
import { setTerminalPref } from '../../lib/terminalPrefs'
import { Dialog } from '../Dialog'
import { useNotice } from '../Notice'
import { Button } from '../ui'

/** Confirmation prompt before multi-line paste, and notices when the browser blocks the clipboard. */
export function ClipboardBridge() {
  const { t } = useTranslation()
  const notify = useNotice()
  const [pending, setPending] = useState<PasteConfirmDetail | null>(null)
  const [dontAsk, setDontAsk] = useState(false)

  useEffect(() => {
    const onConfirm = (event: Event) => {
      setDontAsk(false)
      setPending((event as CustomEvent<PasteConfirmDetail>).detail)
    }
    const onNote = (event: Event) => {
      const { reason } = (event as CustomEvent<ClipboardNoteDetail>).detail
      notify(reason === 'insecure' ? t('clipboard.needsHttps') : t('clipboard.denied'))
    }
    const onCopied = (event: Event) => {
      if (!(event as CustomEvent<CopiedDetail>).detail.ok) notify(t('clipboard.copyFailed'))
    }
    window.addEventListener(PASTE_CONFIRM_EVENT, onConfirm)
    window.addEventListener(CLIPBOARD_NOTE_EVENT, onNote)
    window.addEventListener(COPIED_EVENT, onCopied)
    return () => {
      window.removeEventListener(PASTE_CONFIRM_EVENT, onConfirm)
      window.removeEventListener(CLIPBOARD_NOTE_EVENT, onNote)
      window.removeEventListener(COPIED_EVENT, onCopied)
    }
  }, [notify, t])

  if (!pending) return null
  const lines = pastedLines(pending.text)
  const terminal = peekTerminal(pending.sessionId)

  const close = () => {
    setPending(null)
    terminal?.term.focus()
  }
  const paste = () => {
    if (dontAsk) setTerminalPref('confirmMultiline', false)
    terminal?.term.paste(pending.text)
    close()
  }

  return (
    <Dialog
      open
      title={t('clipboard.confirmTitle', { count: lines.length })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {t('common.cancel')}
          </Button>
          <Button onClick={paste} autoFocus>
            {t('clipboard.paste')}
          </Button>
        </>
      }
    >
      <p className="text-sm text-mist-300">{t('clipboard.confirmLead')}</p>
      <pre className="nt-scroll max-h-60 overflow-auto rounded-xl border border-ink-700 bg-term-bg p-3 font-mono text-xs leading-relaxed text-term-fg">
        {lines.map((line, index) => (
          <div key={index} className="flex gap-3">
            <span className="w-6 shrink-0 text-right text-mist-600 select-none">{index + 1}</span>
            <span className="whitespace-pre">{line || ' '}</span>
          </div>
        ))}
      </pre>
      <label className="flex cursor-pointer items-center gap-2 text-sm text-mist-400">
        <input type="checkbox" className="accent-accent-500" checked={dontAsk} onChange={(event) => setDontAsk(event.target.checked)} />
        {t('clipboard.dontAsk')}
      </label>
    </Dialog>
  )
}
