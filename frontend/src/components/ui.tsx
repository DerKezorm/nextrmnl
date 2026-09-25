/** Basic building blocks in the look of Nexview, nexcrate and nexpulse. */

import type { ComponentPropsWithRef, InputHTMLAttributes, ReactNode } from 'react'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'

import { buttonClasses, type ButtonVariant } from './buttonClasses'
import { Symbol, type SymbolName } from './Symbol'

export const SELECT_CLASS =
  'rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50'

export const INPUT_CLASS =
  'w-full rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 placeholder:text-mist-600 ' +
  'transition-colors focus:border-accent-500 focus:outline-none disabled:opacity-50'

type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: ButtonVariant
  size?: 'md' | 'sm'
  loading?: boolean
}

export function Button({ variant = 'primary', size = 'md', loading = false, className = '', children, disabled, ...rest }: ButtonProps) {
  return (
    <button type="button" className={`${buttonClasses(variant, size)} ${className}`} disabled={disabled || loading} {...rest}>
      {loading && <Spinner />}
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin`} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" opacity=".25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  )
}

export function PageLoading() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-[30vh] items-center justify-center gap-2 text-sm text-mist-600" role="status" aria-live="polite">
      <Spinner />
      <span>{t('common.loading')}</span>
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={'rounded-2xl border border-ink-700 bg-ink-850/80 p-6 shadow-2xl shadow-black/30 backdrop-blur ' + className}>{children}</div>
}

/** A settings section: heading, explanation, content, optionally a switch or button on the right. */
export function Section({ title, intro, aside, children }: { title: string; intro?: ReactNode; aside?: ReactNode; children?: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">{title}</h2>
          {intro && <p className="mt-1 text-sm text-mist-500">{intro}</p>}
        </div>
        {aside && <div className="shrink-0">{aside}</div>}
      </div>
      {children}
    </Card>
  )
}

export function PageHeader({ title, lead, aside }: { title: string; lead?: ReactNode; aside?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        {lead && <p className="mt-1 text-mist-500">{lead}</p>}
      </div>
      {aside}
    </header>
  )
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode; error?: string | null }

export function Field({ label, hint, error, className = '', ...rest }: FieldProps) {
  const id = useId()
  const described = [hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <input id={id} aria-describedby={described} aria-invalid={error ? true : undefined} className={INPUT_CLASS + ' ' + className} {...rest} />
      {error && (
        <p id={`${id}-error`} className="text-xs text-bad-500">
          {error}
        </p>
      )}
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-mist-500">
          {hint}
        </p>
      )}
    </div>
  )
}

export function SelectField({
  label,
  value,
  onChange,
  children,
  disabled,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  children: ReactNode
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} className={SELECT_CLASS}>
        {children}
      </select>
    </div>
  )
}

/**
 * A switch. Warning: named via `aria-labelledby`: `<label for>` does not reliably
 * name a button with `role="switch"`.
 */
export function Switch({
  label,
  hint,
  checked,
  onChange,
  disabled = false,
  hideLabel = false,
}: {
  label: string
  hint?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  hideLabel?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-start justify-between gap-4">
      <div className={hideLabel ? 'sr-only' : 'min-w-0'}>
        <p id={`${id}-label`} className="text-sm font-medium text-mist-200">
          {label}
        </p>
        {hint && <p className="mt-0.5 text-xs text-mist-500">{hint}</p>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={
          'relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-40 ' +
          (checked ? 'bg-accent-500' : 'bg-ink-600')
        }
      >
        <span
          className={
            'absolute top-0.5 left-0.5 h-5 w-5 rounded-full transition-transform ' +
            (checked ? 'translate-x-5 bg-on-accent' : 'bg-white')
          }
        />
      </button>
    </div>
  )
}

export function Banner({ tone = 'info', children }: { tone?: 'info' | 'bad' | 'ok' | 'warn'; children: ReactNode }) {
  const styles = {
    info: 'border-accent-500/30 bg-accent-500/8 text-mist-200',
    ok: 'border-ok-500/40 bg-ok-500/10 text-mist-200',
    warn: 'border-warn-500/40 bg-warn-500/10 text-mist-200',
    bad: 'border-bad-500/40 bg-bad-500/10 text-bad-500',
  }[tone]
  const symbol: SymbolName = tone === 'bad' || tone === 'warn' ? 'warn' : tone === 'ok' ? 'check' : 'info'
  return (
    <div role={tone === 'bad' ? 'alert' : 'status'} className={'flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm ' + styles}>
      <Symbol name={symbol} className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  )
}

export function Badge({ tone = 'neutral', children }: { tone?: 'neutral' | 'accent' | 'bad' | 'ok' | 'warn'; children: ReactNode }) {
  const styles = {
    neutral: 'bg-ink-800 text-mist-300',
    accent: 'bg-accent-500/15 text-accent-400',
    bad: 'bg-bad-500/12 text-bad-500',
    ok: 'bg-ok-500/12 text-ok-500',
    warn: 'bg-warn-500/12 text-warn-500',
  }[tone]
  return <span className={'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ' + styles}>{children}</span>
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-ink-700 px-6 py-10 text-center">
      <p className="font-semibold text-mist-200">{title}</p>
      {children && <div className="mt-2 text-sm text-mist-500">{children}</div>}
    </div>
  )
}
