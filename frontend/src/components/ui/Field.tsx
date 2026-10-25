import { type InputHTMLAttributes, type SelectHTMLAttributes, type ReactNode, useId } from 'react'

// One input style — replaces the ~7 copy-pasted `cls` consts. 44px tall, visible focus ring.
export const inputClass =
  'w-full min-h-11 rounded-xl border border-line bg-surface px-3 text-sm text-ink ' +
  'placeholder:text-ink-3 outline-none transition-colors ' +
  'focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-50'

/** Label-above-field wrapper (never placeholder-as-label) with optional hint + error. */
export function Field({
  label, hint, error, htmlFor, children,
}: { label: string; hint?: string; error?: string; htmlFor?: string; children: ReactNode }) {
  return (
    <label className="block" htmlFor={htmlFor}>
      <span className="mb-1.5 block text-xs font-semibold text-ink-2">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-ink-3">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-negative">{error}</span>}
    </label>
  )
}

export function TextInput({ label, hint, error, ...props }:
  { label?: string; hint?: string; error?: string } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId()
  const input = <input id={id} className={inputClass} {...props} />
  return label ? <Field label={label} hint={hint} error={error} htmlFor={id}>{input}</Field> : input
}

export function SelectInput({ label, hint, error, children, ...props }:
  { label?: string; hint?: string; error?: string } & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId()
  const select = <select id={id} className={inputClass} {...props}>{children}</select>
  return label ? <Field label={label} hint={hint} error={error} htmlFor={id}>{select}</Field> : select
}
