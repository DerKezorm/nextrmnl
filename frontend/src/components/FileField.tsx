import type { ReactNode } from 'react'
import { useId } from 'react'

/** A file field in the look of the other fields. */
export function FileField({ label, accept, hint, onChange }: { label: string; accept?: string; hint?: ReactNode; onChange: (file: File | null) => void }) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <input
        id={id}
        type="file"
        accept={accept}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        className="w-full rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 file:mr-3 file:rounded-full file:border-0 file:bg-ink-800 file:px-3 file:py-1 file:text-sm file:text-mist-300"
      />
      {hint && <p className="text-xs text-mist-500">{hint}</p>}
    </div>
  )
}
