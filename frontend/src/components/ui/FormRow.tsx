import { type FormHTMLAttributes, type ReactNode } from 'react'

/**
 * FormRow — the ONE add/edit form layout. Collapses the seven divergent "Add X" forms (Add loan,
 * asset, income, investment, fund, category, disbursement…) — each of which hand-rolled its own
 * legacy `const cls = 'bg-void-2 border border-hairline…'` — onto a single responsive grid.
 *
 * Compose <Field>/<TextInput>/<SelectInput> (from ./Field, the canonical 44px inputs) as children,
 * then pass the submit control via `submit`. Fields flow: 1-col phone → 2-col sm → configurable at
 * lg. The submit button is right-aligned on its own row so it never fights the fields.
 */
export function FormRow({
  children,
  submit,
  cols = 4,
  className = '',
  ...formProps
}: {
  children: ReactNode
  /** the submit button / actions (use the accent button style) */
  submit?: ReactNode
  /** columns at the lg breakpoint (2 | 3 | 4). Always 1 on phone, 2 from sm. */
  cols?: 2 | 3 | 4
  className?: string
} & FormHTMLAttributes<HTMLFormElement>) {
  const lg = cols === 2 ? 'lg:grid-cols-2' : cols === 3 ? 'lg:grid-cols-3' : 'lg:grid-cols-4'
  return (
    <form
      {...formProps}
      className={`rounded-2xl border border-line bg-surface-2 p-4 ${className}`}
    >
      <div className={`grid grid-cols-1 gap-3 sm:grid-cols-2 ${lg}`}>{children}</div>
      {submit && <div className="mt-4 flex justify-end gap-2">{submit}</div>}
    </form>
  )
}

/**
 * SubmitButton — the canonical primary form action (accent, 44px, rounded-input). Optional so
 * callers can drop in their own, but standardises the common case.
 */
export function SubmitButton({
  children,
  disabled,
  type = 'submit',
  onClick,
}: {
  children: ReactNode
  disabled?: boolean
  type?: 'submit' | 'button'
  onClick?: () => void
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className="min-h-11 rounded-input bg-accent px-5 text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50"
    >
      {children}
    </button>
  )
}
