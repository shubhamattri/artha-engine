import { useQuery } from '@tanstack/react-query'
import { fetchNetworthSummary, formatINR } from '../api/client'
import { Card } from './ui'

/**
 * Gold collateral — the honest balance-sheet view. Your gold keeps its FULL real value even when you
 * borrow against it; a gold loan is a liability, not gold that vanished. So we show:
 *   Real value  → live (grams × today's rate; grows as you buy more / the price rises)
 *   Pledged     → sum of active "Gold Loan" balances (straight from the Debt section) + any pledge held
 *                 outside Artha (a parent's existing loan)
 *   Net value   → real − pledged (your equity)
 *   Borrow room → real × LTV − pledged (how much more you could still raise)
 * Everything recomputes on its own: add or mark-paid a gold loan in Debt and these move automatically.
 */
export default function GoldCollateral() {
  const { data } = useQuery({ queryKey: ['networth-summary'], queryFn: fetchNetworthSummary })
  const g = data?.gold_collateral
  if (!g || g.real_value <= 0) return null

  const rows = [
    { label: 'Real value', value: g.real_value, tone: 'text-ink', hint: 'live · grams × rate' },
    { label: 'Pledged', value: g.pledged_value, tone: 'text-negative', hint: 'against gold loans' },
    { label: 'Net (your equity)', value: g.net_value, tone: 'text-accent', hint: 'real − pledged' },
  ]
  return (
    <Card>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="font-display text-base font-semibold text-ink">Gold collateral</h3>
        <span className="text-[11px] text-ink-3">room to borrow <span className="money font-semibold text-ink-2">{formatINR(g.borrow_room)}</span> @ {Math.round(g.ltv * 100)}% LTV</span>
      </div>
      {/* what it's made of + the live Indian rate it's valued at, and when that rate was captured */}
      <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px] text-ink-3">
        <span className="money font-semibold text-ink-2">{g.total_grams}g</span>
        <span>gold</span>
        {g.holdings.map((h) => (
          h.rate_per_g ? (
            <span key={h.name} className="rounded-chip bg-surface-2 px-1.5 py-0.5">
              {h.purity ?? ''} @ <span className="money">{formatINR(Math.round(h.rate_per_g))}</span>/g
            </span>
          ) : null
        ))}
        {g.priced_at && <span>· rate captured {new Date(g.priced_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span>}
      </div>
      <div className="grid grid-cols-3 gap-3">
        {rows.map((r) => (
          <div key={r.label} className="rounded-2xl border border-line bg-surface-2 p-3 text-center">
            <div className="text-[11px] font-medium text-ink-3">{r.label}</div>
            <div className={`money mt-1 text-sm font-bold tabular-nums ${r.tone}`}>{formatINR(r.value)}</div>
            <div className="mt-0.5 text-[10px] text-ink-3">{r.hint}</div>
          </div>
        ))}
      </div>
      {(g.gold_loans.length > 0 || g.external_pledged > 0) && (
        <div className="mt-3 space-y-1 border-t border-line pt-3 text-[11px] text-ink-3">
          {g.gold_loans.map((l) => (
            <div key={l.name} className="flex justify-between">
              <span>{l.name}</span><span className="money text-negative">−{formatINR(l.balance)}</span>
            </div>
          ))}
          {g.external_pledged > 0 && (
            <div className="flex justify-between">
              <span>Pledged outside Artha (parents’ existing loan)</span>
              <span className="money text-negative">−{formatINR(g.external_pledged)}</span>
            </div>
          )}
        </div>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-ink-3">
        Pledged comes from <span className="font-medium text-ink-2">Gold Loans in the Debt section</span> — add one there
        and it appears here; mark it paid and the gold is un-pledged automatically. Taking a gold loan never lowers your
        real gold value, only what you owe against it.
      </p>
    </Card>
  )
}
