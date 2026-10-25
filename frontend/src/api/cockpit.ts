import { useQuery } from '@tanstack/react-query'
import { api, type DebtMove } from './client'

/**
 * The hero's state, decided by the SERVER.
 *
 * It used to be decided here, by `(spent / days_elapsed) × days_total` — the one projection
 * api/budget.py rejects in its own source, because it multiplies a single lumpy purchase by the rest
 * of the cycle and calls the result a pace. On live data that formula returns Rs 2,19,000 against an
 * allowance of Rs 2,17,500 and colours the hero amber, while the owned projection returns Rs 1,96,000
 * and the household is genuinely on track. Rs 23,000 of difference, and it flipped the verdict.
 *
 * `days_early` is null unless the drip rate genuinely exhausts what is left before the cycle ends, so
 * "runs out early" can never be manufactured out of one big purchase.
 */
export interface Pace {
  allowed_inr: number
  spent_inr: number
  projected_inr: number
  /** how much of the ALLOWANCE is gone */
  spent_pct: number | null
  /** how much of the CYCLE is gone — the whole point of the graphic is that these two differ */
  elapsed_pct: number
  days_early: number | null
  state: 'on_track' | 'tight' | 'over'
}

/** Something only a person can settle. Absent entirely when there is nothing to settle. */
export type NeedsYou =
  | { kind: 'uncategorised'; count: number; amount_inr: number; to: string }
  | { kind: 'duplicates'; count: number; to: string }
  | {
      kind: 'renewal'; count: number; in_days: number; to: string
      policies: { provider: string; kind_of_cover: string; premium_inr: number
                  sum_assured_inr: number; renews_on: string; in_days: number }[]
    }

export interface Cockpit {
  cycle: { label: string; days_left: number; days_total: number; days_elapsed?: number; spent: number }
  safe_to_spend: number
  pace?: Pace
  needs_you?: NeedsYou[]
  glance: { income: number; committed: number; saved: number; leftover: number }
  coming_up: {
    label: string; amount: number; count: number
    items: { name: string; amount: number; day: number | null }[]
  }[]
  move?: DebtMove | null
  remaining_dues?: number
  nudges: { tone: 'negative' | 'warn' | 'accent'; title: string; body: string; cta: string }[]
  ahead: { savings_rate: number; debt_total: number; loan_count: number; sinking_total: number; net_worth: number }
}

export async function fetchCockpit(mode: string = 'cycle'): Promise<Cockpit> {
  const { data } = await api.get<Cockpit>('/cockpit', { params: { mode } })
  return data
}

export function useCockpit(mode: string = 'cycle') {
  return useQuery({ queryKey: ['cockpit', mode], queryFn: () => fetchCockpit(mode) })
}
