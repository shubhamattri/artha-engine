import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchTransactions, formatINR, type CategoryMatrix, type Transaction } from '../api/client'
import EditTxnSheet from './EditTxnSheet'
import { Card, Sheet, TxnRow, ErrorState } from './ui'

// Category heat palette (mirrors cat-1…cat-8; red stays reserved for `negative`).
// The palette lives in lib/strap.ts, once. Three files each carried a private copy of this array and
// all three still had #7A5C82 in it — the plum the design drops entirely — so the ledger was painting
// categories in a colour that is not in the system. A copied constant cannot be corrected.
import { STRAP_PALETTE } from '../lib/strap'
const CAT_COLORS = STRAP_PALETTE
function tint(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

function shortK(v: number): string {
  if (!v) return '—'
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`
  return String(Math.round(v))
}
function mLabel(m: string): string {
  const [y, mo] = m.split('-').map(Number)
  return new Date(y, mo - 1, 1).toLocaleString('en-IN', { month: 'short', year: '2-digit' })
}

/** Month-on-month category matrix. Rows = category, columns = calendar month. Each cell is tappable →
 *  that month's transactions for the category. A subtle ▲/▼ marks a big move off the previous month. */
export default function AnalyticsMatrix({ data, labelFor = mLabel, colHint = 'Month × category' }: {
  data: CategoryMatrix; labelFor?: (key: string) => string; colHint?: string
}) {
  const [cell, setCell] = useState<{ category: string; label: string; ids: number[] } | null>(null)
  const { months, categories } = data
  if (months.length === 0) {
    return <Card><p className="py-8 text-center text-sm text-ink-3">No spending history yet.</p></Card>
  }
  return (
    <Card>
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-xs font-semibold text-ink-2">{colHint}</span>
        <span className="text-[11px] text-ink-3">tap a cell for the transactions</span>
      </div>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full border-collapse text-right">
          <thead>
            <tr className="text-[11px] text-ink-3">
              <th className="sticky left-0 bg-surface px-1 py-1 text-left font-medium">Category</th>
              {months.map((m) => <th key={m} className="px-1.5 py-1 font-medium">{labelFor(m)}</th>)}
              <th className="px-1.5 py-1 font-semibold text-ink-2">Avg</th>
            </tr>
          </thead>
          <tbody>
            {categories.map((c, ci) => {
              const color = CAT_COLORS[ci % CAT_COLORS.length]
              const rowMax = Math.max(1, ...months.map((m) => c.byMonth[m] ?? 0))   // heat is per-row (each category's own scale)
              const idsFor = (m: string) => c.idsByMonth?.[m] ?? []
              return (
                <tr key={c.category} className="border-t border-line">
                  <td className="sticky left-0 max-w-[7.5rem] truncate bg-surface px-1 py-1.5 text-left text-xs font-medium text-ink">
                    <span className="mr-1.5 inline-block h-2 w-2 rounded-chip align-middle" style={{ background: color }} />
                    {c.category}
                  </td>
                  {months.map((m, i) => {
                    const v = c.byMonth[m] ?? 0
                    const prev = i > 0 ? (c.byMonth[months[i - 1]] ?? 0) : 0
                    const jump = prev > 0 && v > 0 ? (v - prev) / prev : 0
                    const arrow = jump >= 0.2 ? '▲' : jump <= -0.2 ? '▼' : ''
                    const yoy = c.yoyByMonth?.[m]      // % vs same month last year — present only with prior-year data
                    return (
                      <td key={m} className="px-0.5 py-1">
                        {v > 0 ? (
                          <button
                            onClick={() => setCell({ category: c.category, label: labelFor(m), ids: idsFor(m) })}
                            /* the YoY figure is rendered inline below as the LY badge, so it needs no hover-only title */
                            style={{ background: tint(color, 0.05 + 0.28 * (v / rowMax)) }}
                            className="money flex min-h-11 w-full flex-col items-center justify-center rounded-md px-1.5 text-xs tabular-nums text-ink hover:ring-1 hover:ring-accent"
                          >
                            {shortK(v)}
                            {arrow && <span className={`ml-0.5 text-[9px] ${arrow === '▲' ? 'text-warn' : 'text-positive'}`}>{arrow}</span>}
                            {yoy != null && <span className={`block text-[8px] leading-none ${yoy > 0 ? 'text-warn' : 'text-positive'}`}>LY {yoy > 0 ? '+' : ''}{yoy}%</span>}
                          </button>
                        ) : (
                          <span className="money px-1.5 text-xs tabular-nums text-ink-3">—</span>
                        )}
                      </td>
                    )
                  })}
                  <td className="money px-1.5 py-1.5 text-xs font-bold tabular-nums text-ink">{shortK(c.avg)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <MatrixDrill cell={cell} onClose={() => setCell(null)} />
    </Card>
  )
}

function MatrixDrill({ cell, onClose }: { cell: { category: string; label: string; ids: number[] } | null; onClose: () => void }) {
  const [editing, setEditing] = useState<Transaction | null>(null)
  // Drill the EXACT rows that compose the cell (by id) — so the list total always reconciles with the
  // number in the grid, regardless of card type / txn type / archival differences.
  const q = useQuery({
    queryKey: ['txns', 'ids', cell?.ids.join(',')],
    queryFn: () => fetchTransactions({ ids: cell!.ids.join(','), limit: 500 }),
    enabled: !!cell && cell.ids.length > 0,
  })
  const txns = q.data?.transactions ?? []
  const total = txns.reduce((s, t) => s + (t.direction === 'credit' ? -t.amount : t.amount), 0)
  return (
    <Sheet open={!!cell} onClose={onClose} title={cell ? `${cell.category} · ${cell.label}` : ''}>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs text-ink-3">{txns.length} transactions</span>
        <span className="money text-sm font-bold text-ink">{formatINR(total)}</span>
      </div>
      {/* Gated on isSuccess, NOT on "not loading and not error". React Query retries 3x with
          backoff, and in the gaps between attempts isLoading is false while isError is still
          false — so an absence-based check printed "No transactions" under a "0 / Rs 0" header
          for several seconds before the error appeared. The empty message may only ever render
          when the fetch actually SUCCEEDED and genuinely returned nothing. */}
      {q.isError ? (
        <ErrorState message="Couldn’t load these transactions." onRetry={() => q.refetch()} />
      ) : !q.isSuccess ? (
        <div className="text-sm text-ink-3">Loading…</div>
      ) : txns.length === 0 ? (
        <div className="py-6 text-center text-sm text-ink-3">No transactions.</div>
      ) : (
        <div className="max-h-[60vh] overflow-y-auto">
          {txns.map((t) => (
            <TxnRow key={t.id}
              description={t.merchant ?? t.description}
              meta={`${t.date} · ${t.bank}`}
              amount={`${t.direction === 'credit' ? '+' : ''}${formatINR(t.amount)}`}
              tone={t.direction === 'credit' ? 'positive' : 'default'}
              onClick={() => setEditing(t)}
            />
          ))}
        </div>
      )}
      <EditTxnSheet txn={editing} onClose={() => setEditing(null)} />
    </Sheet>
  )
}
