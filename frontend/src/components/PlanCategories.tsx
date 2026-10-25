import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  fetchBudget, saveBudget, fetchTransactions, formatINR,
  createCategory, renameCategory, deleteCategory,
  type BudgetItem, type Transaction, type CustomCategory,
} from '../api/client'
import EditTxnSheet from './EditTxnSheet'
import CategoryWeeks from './CategoryWeeks'
import CategoryManager from './CategoryManager'
import { Sheet, PeriodToggle, StatCard, StatGrid, Card, PageGrid, TxnRow, CompactRow, FilterPanel, TextInput, ErrorState, Icon, useConfirm } from './ui'
import { useCan } from '../auth/Can'
import CumulativePace from './CumulativePace'
import { usePeriod } from '../hooks/usePeriod'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'

// Data-driven category palette — rotate by INDEX (never keyed by category name), warm + red-free.
// Mirrors the cat-1…cat-8 tokens in tailwind.config.js (red stays reserved for `negative`).
const CAT_BG = ['bg-cat-1', 'bg-cat-2', 'bg-cat-3', 'bg-cat-4', 'bg-cat-5', 'bg-cat-6', 'bg-cat-7', 'bg-cat-8'] as const

// Anchor = a concrete DATE we address the period by (the cycle/month CONTAINING it). Stepping ±1 month
// on the anchor walks distinct periods with no calendar-vs-cycle overlap.
function fmtISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function todayISO(): string { return fmtISO(new Date()) }
function shiftAnchor(iso: string, delta: number): string {
  const [y, m, dd] = iso.split('-').map(Number)
  return fmtISO(new Date(y, m - 1 + delta, dd))
}
function monthLabel(m: string): string {
  const [y, mo] = m.split('-').map(Number)
  return new Date(y, mo - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' })
}
function cycleLabel(m: string): string {
  return `${monthLabel(m)} cycle`   // fallback before the window loads
}
function windowLabel(start?: string, end?: string): string | null {
  if (!start || !end) return null
  const d = (iso: string) => { const x = new Date(iso); return `${x.getDate()} ${x.toLocaleString('en-IN', { month: 'short' })}` }
  return `${d(start)} – ${d(end)}`   // the true cycle window, e.g. "12 Jun – 11 Jul"
}

/** The Plan = one honest table: per category, what you ALLOWED (target), what you've SPENT this
 *  period, and what's LEFT (or how far you've breached). Tap a row to see the exact transactions
 *  behind the number. Uses the same global lens (This cycle | By month) as Home, so it reconciles. */
export default function PlanCategories() {
  const refresh = useHouseholdRefresh()
  const [anchor, setAnchor] = useState(todayISO())   // the date we address the period by
  const [period] = usePeriod()
  // The lens is honoured for EVERY period: 'By month' → the calendar month containing the anchor;
  // 'This cycle' → the billing cycle containing it. ‹ › step the anchor by a month → previous/next
  // period, no dead step at the cycle boundary. The backend returns is_current + the true window.
  const effectiveMode = period
  const budget = useQuery({ queryKey: ['budget', anchor, effectiveMode], queryFn: () => fetchBudget(anchor, effectiveMode) })
  const isCurrent = budget.data?.is_current ?? true
  const targetMonth = budget.data?.month ?? anchor.slice(0, 7)   // month the targets are keyed by
  const [drill, setDrill] = useState<{ category: string; ids: number[] } | null>(null)   // row being drilled into
  const [editing, setEditing] = useState(false)
  const [targets, setTargets] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [managing, setManaging] = useState(false)
  const mayEditTargets = useCan('budget', 'edit')
  const mayEditCategories = useCan('categories', 'edit')

  // Worst first — over, then will-breach, then comfortable — per the handoff, and never alphabetically.
  // This sorted by spend, which is neither: a large category sitting comfortably inside its target
  // outranked a small one at 300% of it, so the row that needed a decision was below the fold while
  // the row that needed nothing led the list. Rank by trouble, and only then by size.
  //
  // Within a tier, by how far past the target — for `ok` that ratio is < 1 and orders the roomiest
  // last, which is the same "worst first" rule applied one tier down. A category with no target has
  // no ratio to compare, so it falls back to spend and sits at the end of its tier.
  const RANK: Record<string, number> = { over: 0, will_breach: 1, ok: 2 }
  const items = (budget.data?.items ?? []).slice().sort((a, b) => {
    const ra = RANK[a.status ?? 'ok'] ?? 2
    const rb = RANK[b.status ?? 'ok'] ?? 2
    if (ra !== rb) return ra - rb
    const ua = a.limit_inr > 0 ? a.spent_inr / a.limit_inr : -1
    const ub = b.limit_inr > 0 ? b.spent_inr / b.limit_inr : -1
    if (ua !== ub) return ub - ua
    return b.spent_inr - a.spent_inr
  })
  const totTarget = items.reduce((s, i) => s + i.limit_inr, 0)
  const totSpent = items.reduce((s, i) => s + i.spent_inr, 0)   // gross
  const refunds = budget.data?.refunds_inr ?? 0
  const netSpent = budget.data?.net_spent_inr ?? (totSpent - refunds)
  // "Spent" (gross) equals the category rows AND weekly bars (same scope). Refunds → Net are shown as
  // their own cards. Left/Over compares against NET (what actually left your pocket after refunds).
  const totLeft = totTarget - netSpent
  const daysLeft = budget.data?.cycle?.days_left ?? 0
  const breaching = items.filter((i) => i.status === 'over' || i.status === 'will_breach')

  function startEdit() {
    setTargets(Object.fromEntries(items.map((i) => [i.category, String(i.limit_inr || '')])))
    setEditing(true)
  }
  async function persist() {
    setSaving(true)
    try {
      await saveBudget(targetMonth, Object.entries(targets).map(([category, v]) => ({
        category, monthly_limit_inr: parseFloat(v) || 0,
      })))   // targets are saved for the period's month
      refresh()
      setEditing(false)
    } finally { setSaving(false) }
  }

  if (budget.isLoading) return <div className="text-sm text-ink-3">Loading…</div>
  // The whole Plan grid derives from this one query; on error it rendered as no categories at all.
  if (budget.isError || !budget.data) return <ErrorState message="Couldn’t load your plan." onRetry={() => budget.refetch()} />

  return (
    <div className="space-y-4">
      {/* period navigator (‹ picker ›) + lens toggle. ‹ › steps by the ACTIVE lens: previous/next month
          in By-month, previous/next CYCLE in This-cycle. The label reflects the lens. */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1 text-ink">
          <button onClick={() => setAnchor((a) => shiftAnchor(a, -1))} className="inline-flex min-h-11 min-w-11 items-center justify-center text-lg text-ink-2 hover:text-ink" aria-label={`Previous ${effectiveMode === 'cycle' ? 'cycle' : 'month'}`}>‹</button>
          <FilterPanel
            triggerLabel={effectiveMode === 'cycle'
              ? (windowLabel(budget.data?.period_start, budget.data?.period_end) ?? cycleLabel(targetMonth))
              : monthLabel(targetMonth)}
            icon="calendar"
            filters={[{ key: 'month', label: effectiveMode === 'cycle' ? 'Cycle' : 'Month', type: 'month', value: targetMonth, onChange: (v) => v && setAnchor(`${v}-15`) }]}
          />
          <button onClick={() => setAnchor((a) => shiftAnchor(a, 1))} disabled={isCurrent}
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-lg text-ink-2 hover:text-ink disabled:opacity-30" aria-label={`Next ${effectiveMode === 'cycle' ? 'cycle' : 'month'}`}>›</button>
        </div>
        <PeriodToggle />
      </div>

      {/* summary — the 3 primary figures: Allowed → Net spent (what actually left your pocket) → Over/Left.
          Net spent is its own card. Spent + Refunds sit TOGETHER beneath as the reconciliation that
          derives it (Spent − Refunds = Net), only when there are refunds. */}
      {/* StatGrid, not a hand-rolled grid: the cells carry no border or radius of their own any
          more, the grid owns the frame. Left in a plain gap-3 grid they rendered as three bare
          white slabs floating on the page. */}
      <StatGrid>
        <StatCard label="Allowed" value={totTarget} />
        <StatCard label="Net spent" value={netSpent} tone={netSpent > totTarget ? 'negative' : 'default'} />
        <StatCard label={totLeft >= 0 ? 'Left' : 'Over'} value={Math.abs(totLeft)} tone={totLeft < 0 ? 'negative' : 'positive'} />
      </StatGrid>

      {/* The headline answered before the detail: the running total against the budget's own pace.
          Week-by-week bars below are the breakdown, which is only worth reading once you know whether
          the period holds. Live only — a closed period has no pace left to project. */}
      {isCurrent && <CumulativePace data={budget.data} />}
      {/* The reconciliation, per the handoff: gross → refunds → dashed rule → net, with the net line
          naming itself as "the figure above". Two numbers for one period, arrived at differently, is
          how Plan and Analytics came to look like two modules disagreeing; showing the subtraction is
          what stops that. It used to stop at Spent and Refunds and left the reader to do the
          arithmetic, which only works if you already know the identity.

          Still only when there are refunds. With none, gross IS net, and three lines where two are
          the same number is noise pretending to be rigour. */}
      {refunds > 0 && (
        <Card padded={false} className="px-4 py-1.5">
          <CompactRow label="Gross spend" amount={totSpent} />
          <CompactRow label="Refunds" amount={refunds} tone="positive" />
          <div className="my-1 border-t border-dashed border-line-2" />
          <CompactRow
            label={<>Net spent <span className="text-ink-3">— the figure above</span></>}
            amount={netSpent}
          />
        </Card>
      )}
      {isCurrent
        ? <p className="-mt-2 text-center text-[11px] text-ink-3">{daysLeft} days left in {effectiveMode === 'cycle' ? 'cycle' : 'month'}</p>
        : <p className="-mt-2 text-center text-[11px] text-ink-3">{effectiveMode === 'cycle' ? (windowLabel(budget.data?.period_start, budget.data?.period_end) ?? cycleLabel(targetMonth)) : monthLabel(targetMonth)} · closed</p>}

      {/* weekly tracker — am I on track THIS week? */}
      {(budget.data?.weeks?.length ?? 0) > 0 && (
        <Card>
          {(() => {
            const weeks = budget.data!.weeks!
            // A cycle is rarely a whole number of weeks (28/29/30/31 days). Buckets are true 7-day weeks
            // from the cycle start and the leftover days form a shorter FINAL bucket — so a 31-day cycle
            // is 4 weeks + 3 days. Each bar therefore states its own dates, day count and target.
            // Read the published span. This used to re-derive it from the two dates, which meant
            // the backend's inclusive-`end` convention was restated here with nothing binding the
            // two together.
            const dayCount = (w: typeof weeks[number]) => w.span
            const uneven = weeks.some((w) => dayCount(w) !== 7)
            const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            /** "12–18 Jul", or "26 Jul–1 Aug" when the bucket straddles two months. */
            const span = (w: typeof weeks[number]) => {
              const a = new Date(w.start), b = new Date(w.end)
              return a.getMonth() === b.getMonth()
                ? `${a.getDate()}–${b.getDate()} ${MON[b.getMonth()]}`
                : `${a.getDate()} ${MON[a.getMonth()]}–${b.getDate()} ${MON[b.getMonth()]}`
            }
            // ONE scale shared by every bar. Scaling each bar against its own target made a 10-day week
            // look SMALLER than a 7-day week with the same daily spend.
            const cap = Math.max(...weeks.map((w) => Math.max(w.target, w.spent)), 1)
            const cur = weeks.find((w) => w.is_current)
            return (
              <>
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="text-xs font-semibold text-ink-2">Week by week</span>
                  <span className="text-[11px] text-ink-3">
                    {uneven
                      ? `${formatINR(Math.min(...weeks.map((w) => w.target)))}–${formatINR(Math.max(...weeks.map((w) => w.target)))} per bucket`
                      : `~${formatINR(budget.data?.weekly_target ?? 0)}/wk pace`}
                  </span>
                </div>
                <div className="flex items-end gap-2">
                  {weeks.map((w) => {
                    const d = dayCount(w)
                    const h = Math.max(4, Math.round((w.spent / cap) * 56))
                    const th = Math.round((w.target / cap) * 56)   // where this bucket's own target sits
                    const color = w.is_future ? 'var(--line-2)' : w.on_track === false ? 'var(--negative)' : w.is_current ? 'var(--accent)' : 'var(--positive)'
                    return (
                      <div key={w.week} className="flex flex-1 flex-col items-center gap-1">
                        <div className="relative flex h-14 w-full items-end">
                          <div className="w-full rounded-t" style={{ height: h, background: color, opacity: w.is_future ? 0.4 : 1 }} />
                          {/* target tick — without it a longer bucket's taller bar reads as overspend */}
                          <div className="absolute left-0 w-full border-t border-dashed border-ink-3/50"
                               style={{ bottom: Math.min(th, 56) }} title={`target ${formatINR(w.target)}`} />
                        </div>
                        <div className={`text-[10px] leading-tight ${w.is_current ? 'font-bold text-accent' : 'text-ink-3'}`}>
                          W{w.week} · {d}d
                        </div>
                        <div className="text-[9px] leading-tight text-ink-3/80">{span(w)}</div>
                        <div className="money text-[10px] text-ink-3">{w.is_future ? '—' : formatINR(w.spent)}</div>
                        <div className="money text-[9px] text-ink-3/60">of {formatINR(w.target)}</div>
                      </div>
                    )
                  })}
                </div>
                {cur ? (
                  <p className={`mt-2 text-center text-[11px] font-medium ${cur.on_track === false ? 'text-negative' : 'text-positive'}`}>
                    This week: {formatINR(cur.spent)} of {formatINR(cur.target)}
                    {dayCount(cur) !== 7 ? ` (${dayCount(cur)}-day stretch)` : ''} — {cur.on_track === false ? 'over the weekly pace' : 'on track'}
                  </p>
                ) : null}
                {uneven ? (
                  <p className="mt-1 text-center text-[10px] text-ink-3">
                    {/* This line has to answer "why do two 7-day weeks show different figures", or
                        the bar looks broken. It said "each bucket's target is scaled to its own
                        length", which explains the short tail and nothing else. */}
                    {budget.data?.cycle?.days_total ?? 0}-day cycle = {weeks.filter((w) => dayCount(w) === 7).length} full weeks
                    {' '}+ {dayCount(weeks[weeks.length - 1])} days. Each week&rsquo;s line is set when it opens, from what
                    was left at that moment, and holds for the rest of that week — so going over slows every week
                    after it.
                  </p>
                ) : null}
              </>
            )
          })()}
        </Card>
      )}

      {/* Breach alert — a HAIRLINE card, per the handoff, explicitly "not a tinted panel with a
          coloured border". It was `bg-negative/5` inside `border-negative/40`, which paints the whole
          card as the alarm; then the icon, the count and the category names are all saying the same
          thing four times and the card stops being one item on a page of items. The urgency belongs
          to the icon and the count. The surface stays the surface. */}
      {breaching.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-2xl border border-line bg-surface p-3">
          <span className="mt-0.5 text-negative"><Icon name="alert" size={16} /></span>
          <div className="text-sm text-ink">
            <span className="font-semibold text-negative">{breaching.length} {breaching.length === 1 ? 'category' : 'categories'} headed over budget</span>
            <div className="text-xs text-ink-2">{breaching.map((b) => b.category).join(', ')} — at this pace you'll breach before the cycle ends.</div>
          </div>
        </div>
      )}

      {/* The rest of the Plan reads fine without these, so they simply go rather than announcing
          themselves. Each opens an editor, so the button goes with the permission to save. */}
      {(mayEditTargets || mayEditCategories) && (
        <div className="flex items-center justify-end gap-4">
          {mayEditCategories && !editing && (
            <button onClick={() => setManaging(true)} className="inline-flex min-h-11 items-center px-1 text-xs font-semibold text-ink-2 hover:text-ink">
              Manage categories
            </button>
          )}
          {mayEditTargets && (editing ? (
            <button onClick={persist} disabled={saving}
              className="inline-flex min-h-11 items-center rounded-input bg-accent px-3 text-xs font-semibold text-white disabled:opacity-50">
              {saving ? 'Saving…' : 'Save targets'}
            </button>
          ) : (
            <button onClick={startEdit} className="inline-flex min-h-11 items-center px-1 text-xs font-semibold text-accent">Edit targets</button>
          ))}
        </div>
      )}

      {/* category rows — 1 comfortable column on phones, 2 columns on desktop */}
      <PageGrid cols={2} gap="gap-3">
        {items.map((it, i) => (
          <Row key={it.category} it={it} editing={editing} colorClass={CAT_BG[i % CAT_BG.length]}
            target={targets[it.category] ?? ''} onTarget={(v) => setTargets((t) => ({ ...t, [it.category]: v }))}
            onOpen={() => setDrill({ category: it.category, ids: it.ids ?? [] })} />
        ))}
      </PageGrid>

      {/* category × week — "how much on groceries in week 1?" — tap any cell for the exact transactions */}
      <CategoryWeeks anchor={anchor} mode={effectiveMode} />

      <DrillSheet drill={drill} onClose={() => setDrill(null)} />
      <ManageCategoriesSheet open={managing} onClose={() => setManaging(false)} onChanged={refresh} />
    </div>
  )
}

/** Add / rename / delete the household's own budget categories, right from the Plan. A new category is a
 *  first-class variable spend bucket everywhere at once (pickers, Plan, analytics). Deleting one moves its
 *  transactions + budget to Personal / Misc so nothing is orphaned. */
/**
 * The Plan's entry point into category management. The sheet is only the frame; everything a
 * household can do to a category lives in CategoryManager, which grew past what belonged inside this
 * file (create and rename were the whole story when this was written).
 */
function ManageCategoriesSheet({ open, onClose, onChanged }: {
  open: boolean; onClose: () => void; onChanged: () => void
}) {
  return (
    <Sheet open={open} onClose={onClose} title="Manage categories">
      <CategoryManager onChanged={onChanged} />
    </Sheet>
  )
}



function Row({ it, editing, target, onTarget, onOpen, colorClass }: {
  it: BudgetItem; editing: boolean; target: string; onTarget: (v: string) => void; onOpen: () => void; colorClass: string
}) {
  const over = it.status === 'over'
  const willBreach = it.status === 'will_breach'
  const pct = it.limit_inr > 0 ? Math.min(100, (it.spent_inr / it.limit_inr) * 100) : (it.spent_inr > 0 ? 100 : 0)
  const projPct = it.limit_inr > 0 && it.projected_inr ? Math.min(100, (it.projected_inr / it.limit_inr) * 100) : 0
  const barColor = over ? 'var(--negative)' : willBreach ? 'var(--warn)' : 'var(--accent)'
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        {/* Plan's primary interaction -- the header tells you to tap a category. It measured 38px. */}
        <button onClick={onOpen} className="flex min-h-11 min-w-0 flex-1 flex-col justify-center text-left">
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-chip ${colorClass}`} />
            <div className="truncate text-sm font-semibold text-ink">{it.category}</div>
          </div>
          <div className="money mt-0.5 text-xs text-ink-2">
            {formatINR(it.spent_inr)} {it.limit_inr > 0 ? `of ${formatINR(it.limit_inr)}` : '· no target'}
          </div>
        </button>
        {editing ? (
          <input value={target} onChange={(e) => onTarget(e.target.value)} inputMode="decimal" placeholder="target"
            className="w-24 rounded-input border border-line bg-surface-2 px-2 py-1 text-right text-sm text-ink outline-none focus:border-accent" />
        ) : (
          <div className={`money shrink-0 text-right text-xs font-semibold ${over ? 'text-negative' : 'text-positive'}`}>
            {it.limit_inr > 0 ? (over ? `over ${formatINR(it.spent_inr - it.limit_inr)}` : `${formatINR(it.remaining_inr)} left`) : ''}
            <span className="ml-1 text-ink-3">›</span>
          </div>
        )}
      </div>
      {it.limit_inr > 0 && (
        <>
          <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
            {/* Projection ghost — where you land at this pace, at the handoff's literal #B07D1E33.
                Written as a literal, not `bg-warn/20`, because that class does not exist: the palette
                is `warn: 'var(--warn)'` and Tailwind 3.4 cannot compute an alpha on a var(), so it
                emits NO rule at all and the element renders with no background. It was `bg-warn/30`,
                which means this ghost has been invisible for its whole life — the bar has been
                showing only the solid fill while the code read as though it showed both.
                47 more of these are dead across the app; see BLOCKED.md. */}
            {projPct > pct && <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${projPct}%`, background: '#B07D1E33' }} />}
            <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pct}%`, background: barColor }} />
          </div>
          {it.projected_inr != null && (
            <div className={`money mt-1 text-[11px] font-medium ${over ? 'text-negative' : willBreach ? 'text-warn' : 'text-ink-3'}`}>
              On pace for {formatINR(it.projected_inr)}
              {it.projected_inr > it.limit_inr
                ? ` — ${formatINR(it.projected_inr - it.limit_inr)} over target`
                : ` — ${formatINR(it.limit_inr - it.projected_inr)} under`}
              {/* A projection is an estimate, so show its span rather than implying false precision.
                  Low = nothing more is spent; high = the old flat-rate assumption. Only worth showing
                  once the two are meaningfully apart. */}
              {it.projected_low_inr != null && it.projected_high_inr != null
                && it.projected_high_inr - it.projected_low_inr > Math.max(500, it.limit_inr * 0.05) && (
                <span className="ml-1 font-normal text-ink-3">
                  ({formatINR(it.projected_low_inr)}–{formatINR(it.projected_high_inr)})
                </span>
              )}
              {it.projection_confidence === 'low' && (
                <span className="ml-1 font-normal text-ink-3">· early in the cycle, rough</span>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function DrillSheet({ drill, onClose }: { drill: { category: string; ids: number[] } | null; onClose: () => void }) {
  const [editing, setEditing] = useState<Transaction | null>(null)
  // Drill the EXACT rows behind the category this period (by id) → the list total always reconciles with
  // the row, in any lens (cycle/month, current/past).
  const q = useQuery({
    queryKey: ['txns', 'ids', drill?.ids.join(',')],
    queryFn: () => fetchTransactions({ ids: drill!.ids.join(','), limit: 500 }),
    enabled: !!drill && drill.ids.length > 0,
  })
  const txns = q.data?.transactions ?? []
  const total = txns.reduce((s, t) => s + (t.direction === 'credit' ? -t.amount : t.amount), 0)
  return (
    <Sheet open={!!drill} onClose={onClose} title={drill?.category ?? ''}>
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
          {txns.map((t: Transaction) => (
            // tap a transaction → edit it right here, over the drill-down. Closing the editor returns
            // you to this list so you can keep reviewing the category (no jumping to Spending).
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
      {/* editor stacks on top of this drill; closing it drops you right back here to keep reviewing */}
      <EditTxnSheet txn={editing} onClose={() => setEditing(null)} />
    </Sheet>
  )
}
