import { api } from './client'

export interface AffordResult {
  verdict: 'go' | 'tight' | 'hold'
  amount: number
  reason: string
  ai: boolean
  big_ticket?: boolean
  // everyday
  monthly_free?: number
  spent_so_far?: number
  overall_after?: number
  category_after?: number | null
  binds_on_category?: boolean
  remaining_after?: number
  // big purchase
  price?: number
  max_loan?: number
  down_payment?: number
  funding?: { source: string; amount: number }[]
  shortfall?: number
  fundable?: boolean
  emergency_buffer?: number
  new_emi?: number
  existing_emi?: number
  foir_pct?: number
  emi_ok?: boolean
  keep_invested?: boolean
  loan_rate_pct?: number
}

export async function checkAfford(amount: number, category?: string): Promise<AffordResult> {
  const { data } = await api.post<AffordResult>('/afford', { amount, category: category || null })
  return data
}

export async function quickAdd(amount: number, category?: string, note?: string, account?: string) {
  const { data } = await api.post('/transactions/quick', {
    amount, category: category || null, note: note || null, account: account || null,
  })
  return data
}
