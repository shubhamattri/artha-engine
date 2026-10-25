import { useQuery } from '@tanstack/react-query'
import { fetchCashflowSummary } from '../api/client'
import Breakdown, { type BreakdownRow } from './Breakdown'
import { ErrorState } from './ui'

/**
 * Two slices of the same monthly engine (/cashflow/summary), rendered through the shared
 * Breakdown primitive so they share one grammar and can never drift from each other:
 *
 *   variant="flow"    income → minus every outflow → Leftover   (the Overview view)
 *   variant="savings" only the set-aside lines → Set aside / mo  (the Net Worth view)
 */
export default function MonthlyFlow({ variant = 'flow' }: { variant?: 'flow' | 'savings' }) {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['cashflow'], queryFn: fetchCashflowSummary })
  if (isLoading) return <div className="text-sm text-ink-3">Loading…</div>
  // A loading-or-missing guard swallows isError, so a failed fetch spins this skeleton forever
  // rather than saying it broke. States.tsx:5 warns about exactly this shape.
  if (isError || !data) return <ErrorState message="Couldn’t load your cashflow." onRetry={() => refetch()} />

  if (variant === 'savings') {
    const rows = ([
      { label: 'Investments', value: data.invested, hint: 'SIP · RD · PPF', accent: 'muted' },
      { label: 'Unallocated', value: data.unallocated, tip: 'Set aside but not in a tracked instrument yet.', accent: 'muted' },
      { label: 'Sinking funds', value: data.sinking, tip: 'Monthly set-aside for lumpy future expenses.', accent: 'muted' },
      { label: 'Insurance', value: data.insurance, hint: 'protection premiums', accent: 'muted' },
    ] as BreakdownRow[]).filter((r) => r.value > 0)
    const setAside = data.saving + data.sinking + data.insurance
    return (
      <Breakdown
        rows={rows}
        total={{
          label: 'Set aside / month',
          value: setAside,
          tip: 'What leaves your salary each month toward wealth and protection — investments, sinking funds and insurance premiums.',
          accent: 'accent',
        }}
      />
    )
  }

  const rows = ([
    { label: 'Income', value: data.income, hint: 'salary', sign: '+', accent: 'positive' },
    { label: 'EMIs', value: data.emi, hint: 'loans · auto', sign: '−', accent: 'negative' },
    { label: 'Fixed expenses', value: data.fixed, sign: '−', accent: 'negative' },
    { label: 'Savings', value: data.saving, hint: 'SIP · RD · PPF', sign: '−', accent: 'muted' },
    { label: 'Sinking funds', value: data.sinking, sign: '−', accent: 'muted' },
    { label: 'Insurance', value: data.insurance, sign: '−', accent: 'muted' },
  ] as BreakdownRow[]).filter((r) => r.value > 0 || r.label === 'Income')
  return (
    <Breakdown
      rows={rows}
      total={{
        label: 'Leftover for variable spend',
        value: data.leftover,
        tip: 'Income minus every committed outflow above. What is genuinely free to spend or save further.',
        accent: data.leftover < 0 ? 'negative' : 'accent',
      }}
    />
  )
}
