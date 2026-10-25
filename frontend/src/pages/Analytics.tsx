import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchCategoryMatrix, fetchCycleMatrix, fetchRecommendations, fetchTrend, saveBudget, currentMonth,
  formatINR, shortINR,
  type CategoryMatrix, type Recommendation,
} from '../api/client'
import AnalyticsMatrix from '../components/AnalyticsMatrix'
import TrendChart from '../components/TrendChart'
import { usePeriod } from '../hooks/usePeriod'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import {
  PageContainer, SectionHeader, Card, StatGrid, StatCard, FilterPanel, PeriodToggle, Loading, ErrorState,
  Icon,
} from '../components/ui'
import { useCan } from '../auth/Can'

function mLabel(m: string): string {
  const [y, mo] = m.split('-').map(Number)
  return new Date(y, mo - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' })
}
function mLabelShort(m: string): string {
  const [y, mo] = m.split('-').map(Number)
  return new Date(y, mo - 1, 1).toLocaleString('en-IN', { month: 'short', year: '2-digit' })
}
function dayMon(iso: string): string {
  const d = new Date(iso)
  return `${d.getDate()} ${d.toLocaleString('en-IN', { month: 'short' })}`
}
function cycleWindowLabel(start: string, end: string): string {
  return `${dayMon(start)}–${dayMon(end)}`   // the true cycle window, e.g. "12 Jun–11 Jul"
}

const SIGNAL_TONE: Record<Recommendation['signal'], string> = {
  unrealistic: 'text-negative bg-negative/10',
  rising: 'text-warn bg-warn/10',
  falling: 'text-positive bg-positive/10',
  volatile: 'text-ink-2 bg-surface-2',
  steady: 'text-ink-3 bg-surface-2',
}

export default function Analytics() {
  // Same universal lens as Plan/Spending: month-on-month vs cycle-on-cycle. Every figure + the matrix
  // re-slice to the selected lens (billing cycle ≠ calendar month for this household).
  const [period] = usePeriod()
  const byCycle = period === 'cycle'
  const [range, setRange] = useState<{ from: string; to: string } | null>(null)

  const monthQ = useQuery({ queryKey: ['analytics-matrix'], queryFn: () => fetchCategoryMatrix(), enabled: !byCycle })
  const cycleQ = useQuery({ queryKey: ['analytics-cycle-matrix'], queryFn: () => fetchCycleMatrix(12), enabled: byCycle })
  const active = byCycle ? cycleQ : monthQ

  if (active.isLoading) return <PageContainer><Loading rows={4} /></PageContainer>
  if (active.isError || !active.data)
    return <PageContainer><ErrorState onRetry={() => active.refetch()} /></PageContainer>

  // Normalise both lenses into ONE matrix shape (columns = months or cycle close-month keys).
  const base: CategoryMatrix = byCycle
    ? {
        months: cycleQ.data!.cycles.map((c) => c.key),
        grand_total_by_month: cycleQ.data!.grand_total_by_cycle,
        refunds_by_month: cycleQ.data!.refunds_by_cycle,
        categories: cycleQ.data!.categories.map((c) => ({
          category: c.category, total: c.total, avg: c.avg, byMonth: c.byCycle, idsByMonth: c.idsByCycle,
        })),
      }
    : monthQ.data!
  // Cycle columns are labelled by their true window (12 Jun–11 Jul), not the raw close-month key.
  const cycleLabels: Record<string, string> = byCycle
    ? Object.fromEntries(cycleQ.data!.cycles.map((c) => [c.key, cycleWindowLabel(c.start, c.end)]))
    : {}

  // Month lens gets a from/to window; cycle lens shows the last 12 cycles as-is.
  const allCols = base.months
  const from = !byCycle ? (range?.from ?? allCols[0]) : allCols[0]
  const to = !byCycle ? (range?.to ?? allCols[allCols.length - 1]) : allCols[allCols.length - 1]
  const visible = allCols.filter((m) => m >= from && m <= to)

  const view: CategoryMatrix = {
    months: visible,
    grand_total_by_month: base.grand_total_by_month,
    categories: base.categories
      .map((c) => {
        const nz = visible.map((m) => c.byMonth[m] ?? 0).filter((v) => v > 0)
        const total = nz.reduce((s, v) => s + v, 0)
        return { ...c, total: Math.round(total * 100) / 100, avg: nz.length ? Math.round((total / nz.length) * 100) / 100 : 0 }
      })
      .filter((c) => c.total > 0)
      .sort((a, b) => b.total - a.total),
  }
  const totalSpend = visible.reduce((s, m) => s + (base.grand_total_by_month[m] ?? 0), 0)
  // Net the refunds at the window level (matrix cells stay gross) so the headline matches Plan/Cockpit,
  // and the "Spent − Refunds = Net" line makes the gross cells reconcile to it.
  const refundsTotal = visible.reduce((s, m) => s + (base.refunds_by_month?.[m] ?? 0), 0)
  const netSpend = Math.round((totalSpend - refundsTotal) * 100) / 100
  const noun = byCycle ? 'cycle' : 'month'
  // Label the cards with the window the figures actually came from, not just how many columns it had.
  const colLabel = (k: string) => (byCycle ? (cycleLabels[k] ?? k) : mLabelShort(k))
  // A cycle's own label is ALREADY a range ("13 Oct–12 Nov"), so joining two of them produced four
  // dates — "13 Oct-12 Nov – 13 Aug-12 Sept" — which wrapped onto two lines on a phone and read as
  // a range nested inside a range. Across several cycles the window that matters is the first
  // start to the last end, which is one range, two dates, and also what the figure actually covers.
  const cycleSpan = (() => {
    if (!byCycle || visible.length < 2) return null
    const cols = cycleQ.data!.cycles
    const first = cols.find((c) => c.key === visible[0])
    const last = cols.find((c) => c.key === visible[visible.length - 1])
    if (!first || !last) return null
    // Carry the year only when the span crosses one. "13 Oct – 12 Sept" without it reads as a
    // window inside a single year, and this one is eleven months long across two.
    const crosses = first.start.slice(0, 4) !== last.end.slice(0, 4)
    const yr = (iso: string) => (crosses ? ` ${iso.slice(2, 4)}` : '')
    return `${dayMon(first.start)}${yr(first.start)} – ${dayMon(last.end)}${yr(last.end)}`
  })()
  const windowLabel = !visible.length
    ? 'no data yet'
    : cycleSpan ?? (from === to ? colLabel(from) : `${colLabel(from)} – ${colLabel(to)}`)

  return (
    <PageContainer>
      <SectionHeader
        title="History"
        // The lens is a toggle sitting right beside this line, so describing it in prose cost two
        // wrapped lines on a phone and pushed the first real figure past half the viewport.
        sub="Where the money goes, and where your targets should sit."
        action={<PeriodToggle />}
      />


      {/* one shared range filter (From + To inside), in BOTH lenses — a clean short range chip so the
          cycle windows don't crunch (the full windows live in the matrix headers). */}
      <FilterPanel
        triggerLabel={`${mLabelShort(from)} – ${mLabelShort(to)}${byCycle ? ' · cycles' : ''}`}
        icon="calendar"
        filters={[
          { key: 'from', label: 'From', type: 'month', value: from, onChange: (v) => v && setRange({ from: v, to }) },
          { key: 'to', label: 'To', type: 'month', value: to, onChange: (v) => v && setRange({ from, to: v }) },
        ]}
      />

      <StatGrid min="12rem">
        {/* shortINR, matching Debt and Wealth. A multi-cycle total ran to nine digits
            (Rs 12,34,567) while the same magnitude reads Rs 12.35L elsewhere in the app. */}
        <StatCard label="Net spend" value={shortINR(netSpend)}
          sub={`${windowLabel} · ${visible.length} ${noun}${visible.length === 1 ? '' : 's'}`} />
        <StatCard label={`Per ${noun}`} value={shortINR(visible.length ? netSpend / visible.length : 0)}
          sub={`average across ${windowLabel}`} />
      </StatGrid>
      {/* reconcile the gross matrix cells to the net headline: Spent − Refunds = Net (only when refunded) */}
      {refundsTotal > 0 && (
        <Card padded={false} className="px-4 py-1.5 text-[13px]">
          <div className="flex items-center justify-between py-0.5"><span className="text-ink-2">Spent</span><span className="money text-ink">{formatINR(totalSpend)}</span></div>
          <div className="flex items-center justify-between py-0.5"><span className="text-ink-2">Refunds</span><span className="money text-positive">−{formatINR(refundsTotal)}</span></div>
          {/* The line this card exists for, and the one that was missing. Without it the sum stops a
              row short of the figure every other tab shows, so a cycle reading one figure against
              Plan's another looks like two modules disagreeing when the entire difference is the
              refunds listed directly above it. */}
          <div className="mt-0.5 flex items-center justify-between border-t border-line py-1">
            <span className="text-ink">Net · same figure as Plan</span>
            <span className="money font-medium text-ink">{formatINR(netSpend)}</span>
          </div>
          <div className="pb-1 text-[11px] text-ink-2">Bars and cells below are before refunds.</div>
        </Card>
      )}

      <SpendBars cols={visible} totals={base.grand_total_by_month}
        labelFor={byCycle ? ((k) => cycleLabels[k] ?? k) : mLabelShort} noun={noun} />

      <AnalyticsMatrix data={view} labelFor={byCycle ? ((k) => cycleLabels[k] ?? k) : mLabel}
        colHint={byCycle ? 'Cycle × category' : 'Month × category'} />

      <InOut />

      <Recommendations cycleMode={byCycle ? 'instrument_cycle' : 'calendar_month'} noun={noun} />
    </PageContainer>
  )
}

/**
 * In vs out — the only question on this screen the matrix cannot answer.
 *
 * Everything above is variable spend: the everyday categories, the ones a target is set against. This
 * counts every SPEND row, rent and insurance included, against the money that actually arrived. So it
 * is deliberately a BIGGER number than "Net spend" at the top, and the sub-line has to say so — the
 * Money Slice contract's own rule is that all-household-spend must be labelled or it gets read as the
 * budget. It ignores the lens because the endpoint is calendar-month only.
 */
function InOut() {
  const mayView = useCan('spending')
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['analytics-trend'], queryFn: fetchTrend, enabled: mayView,
  })
  if (!mayView) return null
  if (isLoading) return <Card><p className="text-sm text-ink-3">Loading…</p></Card>
  if (isError || !data) return <Card><ErrorState onRetry={() => refetch()} /></Card>
  if (data.months.length < 2) return null

  return (
    <Card>
      <SectionHeader level={2} title="In vs out"
        sub="What arrived against ALL spend — rent and insurance included, so it runs higher than Net spend above. Calendar months, whichever lens is selected." />
      <div className="mt-3">
        <TrendChart months={data.months} />
      </div>
    </Card>
  )
}

const shortRs = (v: number): string => shortINR(v)
// Plain CSS bars (same pattern as Plan's "Week by week") — heights scale to the peak, full control.
function SpendBars({ cols, totals, labelFor, noun }: {
  cols: string[]; totals: Record<string, number>; labelFor: (k: string) => string; noun: string
}) {
  if (cols.length < 2) return null
  const vals = cols.map((c) => Math.round(totals[c] ?? 0))
  const peak = Math.max(...vals, 1)
  const H = 150
  return (
    <Card>
      <div className="mb-3 text-xs font-semibold text-ink-2">Spend by {noun}</div>
      <div className="flex items-end gap-1.5 overflow-x-auto">
        {cols.map((c, i) => (
          <div key={c} className="flex min-w-[2.5rem] flex-1 flex-col items-center gap-1">
            <div className="money text-[9px] tabular-nums text-ink-3">{vals[i] ? shortRs(vals[i]) : ''}</div>
            <div className="flex w-full items-end" style={{ height: H }}>
              <div className="w-full rounded-t transition-all" title={formatINR(vals[i])}
                style={{ height: `${Math.max(2, (vals[i] / peak) * H)}px`, background: vals[i] >= peak ? '#B07D1E' : '#0F7A52' }} />
            </div>
            <div className="text-[9px] leading-tight text-ink-3">{labelFor(c)}</div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function Recommendations({ cycleMode, noun }: { cycleMode: string; noun: string }) {
  const refresh = useHouseholdRefresh()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['recommendations', cycleMode], queryFn: () => fetchRecommendations(cycleMode),
  })
  const apply = useMutation({
    mutationFn: (r: Recommendation) => saveBudget(currentMonth(), [{ category: r.category, monthly_limit_inr: r.suggested }]),
    onSuccess: () => refresh(),
  })
  // The suggestions themselves are worth reading either way; only writing one into the plan goes.
  const mayEdit = useCan('budget', 'edit')

  if (isLoading) return <Card><p className="text-sm text-ink-3">Learning from your history…</p></Card>
  if (isError || !data) return <Card><ErrorState onRetry={() => refetch()} /></Card>

  const recs = data.recommendations
  return (
    <Card>
      <SectionHeader level={2} title="Suggested targets"
        sub={`Median of ${data.period?.label ?? `your last ${data.window} clean ${data.unit ?? noun}s`} per category — spikes and the current part-${data.unit ?? noun} excluded. Follows the ${cycleMode === 'instrument_cycle' ? 'This cycle' : 'By month'} lens.`} />

      {recs.length === 0 && <p className="mt-3 text-sm text-ink-3">Not enough clean history yet to suggest targets. Keep logging — this sharpens every month.</p>}

      <div className="mt-3 space-y-2.5">
        {recs.map((r) => {
          const moved = r.current_target > 0 && Math.abs(r.suggested - r.current_target) >= 1
          return (
            <div key={r.category} className="rounded-2xl border border-line bg-surface p-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-ink">{r.category}</span>
                    <span className={`rounded-chip px-1.5 py-0.5 text-[10px] font-semibold tracked ${SIGNAL_TONE[r.signal]}`}>{r.signal}</span>
                  </div>
                  <div className="money mt-1 text-xs text-ink-2">
                    {r.current_target > 0 ? <>{formatINR(r.current_target)} <span className="text-ink-3">→</span> </> : null}
                    <span className="font-semibold text-ink">{formatINR(r.suggested)}</span>
                    <span className="ml-1 text-ink-3">· band {formatINR(r.band[0])}–{formatINR(r.band[1])}</span>
                  </div>
                  <p className="mt-1 text-[11px] leading-snug text-ink-3">{r.why}</p>
                </div>
                {moved && mayEdit && (
                  <button onClick={() => apply.mutate(r)} disabled={apply.isPending}
                    className="inline-flex min-h-11 shrink-0 items-center rounded-input bg-accent px-3 text-[11px] font-semibold text-surface disabled:opacity-50">
                    Apply
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {data.new_categories.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-semibold text-ink-2">New categories detected</p>
          <p className="mt-1 text-[11px] text-ink-3">
            {data.new_categories.map((c) => `${c.category} (~${formatINR(c.suggested)}/mo)`).join(' · ')} — set a target on Plan.
          </p>
        </div>
      )}
      {data.move_to_fixed.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold text-ink-2">Looks fixed, not variable</p>
          <p className="mt-1 text-[11px] text-ink-3">
            {data.move_to_fixed.map((c) => `${c.category} (~${formatINR(c.avg)}/mo, near-constant)`).join(' · ')} — consider moving to a fixed commitment.
          </p>
        </div>
      )}
    </Card>
  )
}
