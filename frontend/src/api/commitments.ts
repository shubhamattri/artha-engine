import { api } from './client'

/**
 * Everything the household is committed to each month, from every module at once.
 *
 * The pieces already existed and lived apart: loans on the Debt tab, SIPs under Wealth, rent and
 * fixed bills in the Plan ledgers, insurance under its own manager. Nothing added them up, so
 * "what do we owe every month before we spend anything" had no single answer.
 *
 * `GET /commitments/reconcile` covers the same population but only after matching each one against
 * real debits for a chosen month, which is a different and more expensive question. This is the
 * standing list.
 */
export interface Commitment {
  /** "loan:3" | "cashflow:8" | "recurring:12" | "insurance:1" — module and id, so it can be traced. */
  ref: string
  kind: 'EMI' | 'Fixed' | 'Saving' | 'SIP' | 'Insurance' | string
  name: string
  amount: number
  category: string | null
  /** Built server-side from a set, so the order means nothing. Never index into it. */
  types: string[]
}

export async function fetchCommitments(month?: string): Promise<{
  commitments: Commitment[]
  monthly_total: number
}> {
  const { data } = await api.get('/commitments', { params: month ? { month } : {} })
  return data
}
