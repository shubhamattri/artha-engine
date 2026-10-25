import { type ReactNode, type SelectHTMLAttributes, useId } from 'react'

/**
 * FilterBar — the ONE filter container. Two modes, one API:
 *
 * 1. DEFAULT (padded card) — legacy. Wraps <SelectInput>/<TextInput> (the 44px inputs from ./Field)
 *    in a responsive 2/4-up grid inside a rounded card. Kept for backwards-compat (Transactions).
 *
 * 2. COMPACT (`compact`) — the LIGHT variant. A single tidy inline row of small select "chips"
 *    (compose <FilterSelect> as children) with an inline "Clear ✕" affordance. No big padded card
 *    eating a whole block — it sits on one line and wraps only when it must. Prefer this everywhere.
 *
 *    <FilterBar compact onClear={reset}>
 *      <FilterSelect label="Type" value={t} onChange={…}>…</FilterSelect>
 *      <FilterSelect label="Category" value={c} onChange={…}>…</FilterSelect>
 *    </FilterBar>
 */
export function FilterBar({
  children,
  onClear,
  clearLabel = 'Clear filters',
  active = true,
  compact = false,
  className = '',
}: {
  children: ReactNode
  /** show a clear button that calls this */
  onClear?: () => void
  clearLabel?: string
  /** enable/disable the clear button (e.g. no filters applied) */
  active?: boolean
  /** render the light inline-chip row instead of the padded card */
  compact?: boolean
  className?: string
}) {
  if (compact) {
    // Tidy aligned grid of small filter cells — 2-up on phones, inline 4-up on wider screens. No
    // card, no full-width stacking; every cell lines up.
    return (
      <div className={`grid grid-cols-2 gap-2 sm:grid-cols-4 ${className}`}>
        {children}
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            disabled={!active}
            className="inline-flex min-h-11 items-center justify-center gap-1 rounded-input border border-line px-3 text-xs font-semibold text-ink-3 transition-colors hover:border-accent hover:text-ink disabled:opacity-40"
          >
            {clearLabel} <span aria-hidden>✕</span>
          </button>
        )}
      </div>
    )
  }

  return (
    <div className={`rounded-2xl border border-line bg-surface p-3 sm:p-4 ${className}`}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{children}</div>
      {onClear && (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={onClear}
            disabled={!active}
            className="min-h-11 rounded-input px-3 text-xs font-semibold text-ink-2 hover:text-ink disabled:opacity-40"
          >
            {clearLabel}
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * FilterSelect — the small inline select "chip" for a compact <FilterBar>. A native <select> (so it
 * stays accessible + keyboard-friendly) wrapped in a rounded, hairline pill with an inline label.
 * NOT the 44px label-above SelectInput — this is deliberately light so a row of them reads as chips.
 */
export function FilterSelect({
  label,
  className = '',
  children,
  ...props
}: { label?: string; className?: string } & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId()
  return (
    <span
      className={`flex min-h-11 w-full items-center gap-1.5 rounded-input border border-line bg-surface px-3 text-xs transition-colors focus-within:border-accent ${className}`}
    >
      {label && (
        <label htmlFor={id} className="shrink-0 font-medium text-ink-3">
          {label}
        </label>
      )}
      <select
        id={id}
        className="min-w-0 flex-1 cursor-pointer bg-transparent py-1 text-right text-xs font-semibold text-ink outline-none"
        {...props}
      >
        {children}
      </select>
    </span>
  )
}
