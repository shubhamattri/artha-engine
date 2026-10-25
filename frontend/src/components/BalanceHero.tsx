/**
 * The merge, in one card: own and owe side by side, under the number they produce.
 *
 * These were two tabs. Wealth's hero was already assets MINUS liabilities, and those liabilities had
 * their own tab under a competing hero — so the app performed the same subtraction twice and invited
 * you to read each half as a standalone verdict. Side by side under the result, neither half can be
 * mistaken for the whole.
 *
 * The estimate share is on the card rather than buried in the composition below it, because a net
 * worth is only as solid as its inputs and "how much of this is a real price" belongs next to the
 * figure it qualifies. `live_priced: false` means accrued or marked, not what anyone would pay today.
 */
import { useQuery } from '@tanstack/react-query'

import {
  fetchDebt, fetchNetWorthComposition, fetchNetWorthSummary, formatINR, shortINR,
} from '../api/client'
import { Card, ErrorState, Loading } from './ui'

export default function BalanceHero() {
  const comp = useQuery({ queryKey: ['networth-composition'], queryFn: fetchNetWorthComposition })
  const sum = useQuery({ queryKey: ['networth-summary'], queryFn: fetchNetWorthSummary })
  const debt = useQuery({ queryKey: ['debt'], queryFn: fetchDebt })

  if (comp.isLoading || sum.isLoading) return <Loading rows={2} />
  if (comp.isError || !comp.data || sum.isError || !sum.data)
    return <ErrorState message="Couldn’t load your balance." onRetry={() => { comp.refetch(); sum.refetch() }} />

  const c = comp.data
  const assetCount = c.buckets.reduce((a, b) => a + (b.count || 0), 0)
  // The loan count comes from the debt engine's own definition of open, not from a length: the
  // liabilities list on /networth/summary includes settled rows, and counting those would tell the
  // household it owes on loans it has closed.
  const loans = (debt.data?.loans ?? []).filter((l) => !l.closed && l.balance_inr > 0)

  return (
    <Card className="!p-5 sm:!p-6">
      <div className="tracked text-[10px] text-ink-3">Net worth</div>
      <div className="money mt-1 text-[clamp(2rem,9vw,2.75rem)] font-bold leading-none text-ink">
        {formatINR(c.net_worth)}
      </div>
      <div className="mt-1.5 text-sm text-ink-2">what you own, minus what you owe</div>

      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line">
        <div className="bg-surface px-3 py-2.5">
          <div className="tracked text-[10px] text-ink-3">Own</div>
          <div className="money mt-0.5 text-lg font-bold text-accent">{shortINR(c.total_assets)}</div>
          <div className="mt-0.5 text-[11px] text-ink-3">
            <span className="money">{assetCount}</span> holdings
          </div>
        </div>
        <div className="bg-surface px-3 py-2.5">
          <div className="tracked text-[10px] text-ink-3">Owe</div>
          <div className="money mt-0.5 text-lg font-bold text-negative">{shortINR(c.total_liabilities)}</div>
          <div className="mt-0.5 text-[11px] text-ink-3">
            <span className="money">{loans.length}</span> {loans.length === 1 ? 'loan' : 'loans'}
          </div>
        </div>
      </div>

      {c.estimated_pct > 0 && (
        <p className="mt-2.5 text-[11px] leading-relaxed text-ink-3">
          <span className="money font-semibold text-warn">{Math.round(c.estimated_pct)}%</span> of what
          you own is an estimate, not a live price.
        </p>
      )}
    </Card>
  )
}

/**
 * Debt as a peer block, not a tab.
 *
 * It keeps everything it had — strategies, per-loan schedules, prepayment logging — one tap down, and
 * loses only the dark slab that made it look like a different app. The loans get a rate-sorted bar so
 * the dearest one is visibly the dearest, which is the whole argument the strategy picker was making
 * in four cards of prose.
 */
export function DebtBlock() {
  const debt = useQuery({ queryKey: ['debt'], queryFn: fetchDebt })
  if (debt.isLoading) return <Loading rows={2} />
  if (debt.isError || !debt.data) return null

  const d = debt.data
  const loans = (d.loans ?? [])
    .filter((l) => !l.closed && l.balance_inr > 0)
    .sort((a, b) => b.rate_pct - a.rate_pct)
  if (!loans.length) return null
  const total = loans.reduce((a, l) => a + l.balance_inr, 0) || 1

  return (
    <Card>
      {/* Not the total. The hero states it two inches above, in the Owe cell, and restating it here
          makes the block look like a second opinion on the same number. What this block knows that the
          hero cannot is what the debt COSTS to carry and which loan is the dearest — so it leads with
          the interest, which is the figure the payoff order is actually argued from. */}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-xs font-semibold text-ink-2">Debt</div>
        <div className="text-[11px] text-ink-3">
          costing <span className="money font-semibold text-negative">{shortINR(d.monthly_interest)}</span>/mo
          {d.total_emi > 0 && (
            <> · <span className="money font-semibold text-ink-2">{shortINR(d.total_emi)}</span> EMI</>
          )}
        </div>
      </div>

      {/* Rate-sorted, and WIDTH is balance — so the bar says two things at once: which loan is
          dearest (leftmost) and which is biggest (widest). The costliest being small is exactly the
          case the avalanche argument exists for, and this is where you can see it. */}
      <div className="mt-3 flex h-2.5 overflow-hidden rounded-full border border-line">
        {loans.map((l, i) => (
          <span key={l.id} title={`${l.name} · ${l.rate_pct}%`}
                style={{
                  width: `${(l.balance_inr / total) * 100}%`,
                  background: i === 0 ? 'var(--negative)' : i === 1 ? 'var(--warn)' : 'var(--line-2)',
                }} />
        ))}
      </div>

      {/* The bar draws every loan; the list names the four dearest. Anything cut has to SAY it was
          cut — a list of four above a bar of eight reads as "these are your loans", and the rest of
          the balance then looks unaccounted for. */}
      <div className="mt-3 space-y-1.5">
        {loans.slice(0, 4).map((l, i) => (
          <div key={l.id} className="flex items-center gap-2 text-[13px]">
            <span className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: i === 0 ? 'var(--negative)' : i === 1 ? 'var(--warn)' : 'var(--line-2)' }} />
            <span className="min-w-0 flex-1 truncate text-ink">{l.name}</span>
            <span className="money shrink-0 text-xs font-semibold text-ink-2">{l.rate_pct}%</span>
            <span className="money shrink-0 text-right text-xs font-semibold tabular-nums text-ink">
              {shortINR(l.balance_inr)}
            </span>
          </div>
        ))}
        {loans.length > 4 && (
          <div className="pt-0.5 text-[11px] text-ink-3">
            + <span className="money">{loans.length - 4}</span> more, worth{' '}
            <span className="money">{shortINR(loans.slice(4).reduce((a, l) => a + l.balance_inr, 0))}</span>
            {' '}— all of them in the payoff plan below
          </div>
        )}
      </div>
    </Card>
  )
}
