import { useQuery } from '@tanstack/react-query'

import { fetchHealth, formatINR } from '../api/client'
import { strapColor, strapSegments, strapWidths } from '../lib/strap'
import { Card, Strap } from './ui'

/**
 * Where every rupee of income goes — the first of the allocation strap's four uses.
 *
 * Two things were wrong here and both were arithmetic dressed up as styling.
 *
 * The bar rendered `width: ${b.pct}%` straight from the server. Insurance is Rs 2,500 of Rs 5,00,000
 * = 0.45%, which is 1.4px on a 307px bar: rounded away. The legend listed five rows and the bar drew
 * four, which reads as broken data rather than as a small number. `Strap` floors it to a visible
 * segment and pays for the lift out of the segments with headroom.
 *
 * And it painted each segment with the colour the API sends, which is Loan EMIs in terracotta
 * (#C0533B) and Insurance in purple (#7C5CBF). Red only ever means money going the wrong way, and an
 * EMI is a commitment, not a loss; purple is not in the palette at all. Colour now rotates by INDEX
 * over the shared palette. The API still sends `color` — changing that is a contract change — and this
 * component simply stops reading it.
 */
export default function IncomeHealth() {
  const q = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const h = q.data
  if (!h || !h.income) return null

  const items = h.buckets.map((b) => ({ label: b.label, value: b.amount }))
  // The rows have to sit in the strap's order, or the reader maps between a largest-first bar and a
  // server-ordered list. One sort, used by both.
  const { segments } = strapSegments(items)
  const widths = strapWidths(segments.map((s) => s.value))

  return (
    <div>
      <h2 className="mt-7 mb-2.5 text-xs font-bold uppercase tracking-[0.09em] text-ink-3">Where your income goes</h2>
      <Card>
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs font-semibold text-ink-3">Monthly income</div>
            <div className="money mt-1 text-3xl font-bold text-ink">{formatINR(h.income)}</div>
          </div>
          <span className="rounded-chip bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-ink-3">100% allocated</span>
        </div>

        <div className="mt-4">
          <Strap items={items} height={12} loading={q.isLoading} />
        </div>

        {/* breakdown — tidy aligned rows in the same card, not individual cards */}
        <div className="mt-4 border-t border-line pt-1">
          {segments.map((s, i) => (
            <div key={`${s.label}-${i}`} className="flex items-center gap-3 border-b border-line py-2.5 last:border-0">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: strapColor(i) }} />
              <span className="flex-1 truncate text-sm text-ink">{s.label}</span>
              {/* The strap's own width, not the server's raw pct — so a floored segment's row agrees
                  with the segment the reader is looking at. */}
              <span className="money w-12 shrink-0 text-right text-xs tabular-nums text-ink-3">
                {widths[i].toFixed(1)}%
              </span>
              <span className="money w-28 shrink-0 text-right text-sm font-semibold tabular-nums text-ink">
                {formatINR(s.value)}
              </span>
            </div>
          ))}
        </div>
      </Card>
      <p className="mt-3 text-[11px] text-ink-3">Live — updates as your loans, savings, investments or income change.</p>
    </div>
  )
}
