import { useQueryClient } from '@tanstack/react-query'

/** Every React Query key that depends on the household's month-level financial state. Editing any one
 *  entity (cashflow line, sinking fund, asset, investment, insurance policy, budget target) can move
 *  the shared engine numbers, so a single edit must refresh ALL of these — not just its own tab. */
export const HOUSEHOLD_QUERY_KEYS = [
  'cockpit',
  'cashflow',
  'recurring',
  'sinking',
  'networth',
  'disposals',           // recording or correcting a sale moves net worth AND the receivable line
  'health',
  'budget',
  'category-weeks',
  'categories',          // the shared picker list (adding/renaming a category must reach every dropdown)
  'custom-categories',   // the Plan's manage-categories list
  'analytics-matrix',
  'analytics-cycle-matrix',
  'recommendations',
  'category-consistency',
  'duplicate-review',
  'insights',
  'debt',
  'windfall',            // reads the loan book and the cash balance, so any edit to either moves it
  'commitments-reconcile',
  'debt-move-narration',
  'txns',
  'txn-facets',
  'me',
] as const

/** Returns a function that invalidates every household-month-dependent query at once, so editing one
 *  financial entity keeps every other tab (cockpit, commitments, debt, health, budget, insights) in sync. */
export function useHouseholdRefresh(): () => void {
  const qc = useQueryClient()
  return () => {
    for (const key of HOUSEHOLD_QUERY_KEYS) {
      qc.invalidateQueries({ queryKey: [key] })
    }
  }
}
