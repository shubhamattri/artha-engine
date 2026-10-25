import { useState, type ReactNode } from 'react'

/**
 * AddPanel — the ONE collapsible "＋ Add X" affordance. Closed by default it renders as a single
 * canonical accent-outline button ("＋ {label}"); clicking expands it in-place to reveal the form
 * (children) with an accent header + a "Close" control, and collapses again.
 *
 * This is the extraction of the good pattern already living in Debt.tsx (the "+ Add loan" button
 * that toggles <AddLoanForm/>). Every manager that currently renders its add/edit <FormRow> OPEN by
 * default (Cashflow, Recurring, Sinking, BudgetPlan, PlanCategories…) should wrap that form in an
 * <AddPanel> so the page opens tidy and the form only appears on intent — matching Debt.
 *
 * Usage:
 *   <AddPanel label="Add income">
 *     <FormRow submit={<SubmitButton …>Add</SubmitButton>}>…fields…</FormRow>
 *   </AddPanel>
 */
export function AddPanel({
  label,
  children,
  className = '',
  defaultOpen = false,
}: {
  /** button + header text, e.g. "Add loan" (the "＋" glyph is added automatically) */
  label: string
  /** the form to reveal — typically a <FormRow> */
  children: ReactNode
  className?: string
  /** open on mount (rare — the whole point is closed-by-default) */
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`inline-flex min-h-11 items-center gap-1.5 rounded-input border border-line px-3 text-xs font-semibold text-ink-2 transition-colors hover:border-accent hover:text-accent ${className}`}
      >
        <span aria-hidden className="text-sm leading-none">＋</span>
        {label}
      </button>
    )
  }

  return (
    <div className={`space-y-3 ${className}`}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-accent">{label}</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="min-h-11 rounded-input px-2 text-xs font-semibold text-ink-3 transition-colors hover:text-ink"
        >
          Close ▲
        </button>
      </div>
      {children}
    </div>
  )
}

export default AddPanel
