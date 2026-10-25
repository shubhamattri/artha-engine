/**
 * The running total against the budget's own pace — not each week on its own.
 *
 * Week-by-week bars answer "was that week expensive". They cannot answer the question a household
 * actually opens the Plan with, which is "at this rate, does the month hold" — because a week that
 * looks fine on its own can still be the one that puts the cumulative line through the ceiling.
 *
 * Five marks, each earning its place:
 *   ceiling    the whole-period target, flat
 *   pace       the straight line the budget implies, corner to ceiling
 *   spent      the actual running total, week by week, up to today
 *   lands      where this pace ends the period — dashed, because it has not happened
 *   crossing   the point the projection meets the ceiling, if it does so inside the period
 *
 * Every figure comes from api/budget, which owns spend AND the projection. The projection counts
 * one-off lumps once and extrapolates only the drip; the naive spent/elapsed*total would put this
 * household Rs 23,000 further along and change the verdict.
 */
import { type BudgetResponse, formatINR, shortINR } from '../api/client'
import { Card } from './ui'

const W = 320
const H = 140

function dayMon(iso: string): string {
  const d = new Date(iso)
  return `${d.getDate()} ${d.toLocaleString('en-IN', { month: 'short' })}`
}

export default function CumulativePace({ data }: { data: BudgetResponse }) {
  const ceiling = data.total_target_inr ?? 0
  const spent = data.net_spent_inr ?? 0
  const proj = data.projection
  const cyc = data.cycle
  const weeks = data.weeks ?? []
  if (!ceiling || !proj || !cyc || !cyc.days_total || !weeks.length) return null

  // The vertical scale has to hold whichever is taller, or an overshoot draws off the top of the box
  // and the chart silently stops being about the thing it is warning you about.
  const vmax = Math.max(ceiling, proj.projected_inr) || 1
  const y = (v: number) => H - (v / vmax) * H
  const x = (dayFrac: number) => dayFrac * W

  const todayFrac = Math.min(1, (cyc.days_elapsed ?? 0) / cyc.days_total)
  const start = data.period_start ? new Date(data.period_start) : null

  // Cumulative, and only up to today: plotting future buckets at zero would draw the running total
  // falling back to flat, which is the one shape a cumulative line can never have.
  const pts: { fx: number; v: number }[] = [{ fx: 0, v: 0 }]
  let run = 0
  for (const w of weeks) {
    if (w.is_future) break
    run += w.spent ?? 0
    const end = new Date(w.end)
    const frac = start ? Math.min(todayFrac, ((end.getTime() - start.getTime()) / 86400000 + 1) / cyc.days_total) : todayFrac
    pts.push({ fx: frac, v: run })
  }
  // Today's own point, so the line ends where the vertical rule is rather than at the last week's end.
  if (pts[pts.length - 1].fx < todayFrac || run !== spent) pts.push({ fx: todayFrac, v: spent })

  const line = pts.map((p) => `${x(p.fx).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
  const area = `M0,${H} ${line.split(' ').map((s) => `L${s}`).join(' ')} L${x(todayFrac).toFixed(1)},${H} Z`
  const ahead = proj.ahead_by_inr

  return (
    <Card>
      <div className="text-xs font-semibold text-ink-2">Where this cycle lands</div>
      <p className="mt-1 text-[11.5px] leading-relaxed text-ink-2">
        Running total against the budget&rsquo;s own pace — not each week on its own.
      </p>

      {/* The two markers are HTML, not SVG.

          The lines can be stretched — x is time, y is money, and a smeared diagonal still reads as a
          diagonal. A circle cannot: under a 3.2x horizontal scale both dots rendered as wide
          ellipses, and `vectorEffect="non-scaling-stroke"` does not help because it corrects the
          OUTLINE, not the geometry. Positioned in percentages on top of the box they stay round at
          any width, with no resize measurement anywhere. */}
      <div className="relative mt-3.5" style={{ height: H }}>
        {/* preserveAspectRatio="none" because the axes are not the same kind of quantity: x is time,
            y is money, and there is no shape here whose proportions carry meaning. Left at the default
            the 320x140 viewBox scaled to the fixed 140px height and then sat letterboxed in the middle
            of a 1030px card, using a third of the width it was given. The strokes opt out of the
            stretch so a 2.5px line stays 2.5px instead of being smeared horizontally. */}
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
             className="block h-[140px] w-full overflow-visible"
             role="img" aria-label="Cumulative spend against the budget's pace">
          <line x1="0" y1={y(ceiling)} x2={W} y2={y(ceiling)} stroke="var(--line-2)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <line x1="0" y1={H} x2={W} y2={y(ceiling)} stroke="var(--ink-3)" strokeWidth="1.5"
                strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
          <path d={area} fill="var(--accent)" opacity="0.10" />
          <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="2.5"
                    strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
          <line x1={x(todayFrac)} y1={y(spent)} x2={W} y2={y(proj.projected_inr)}
                stroke={proj.over_inr > 0 ? 'var(--warn)' : 'var(--accent-2)'} strokeWidth="2"
                strokeDasharray="2 3" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <line x1={x(todayFrac)} y1="0" x2={x(todayFrac)} y2={H} stroke="var(--ink-3)"
                strokeWidth="1" opacity="0.35" vectorEffect="non-scaling-stroke" />
        </svg>
        {proj.runs_out_at != null && (
          <span className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full"
                style={{ left: `${proj.runs_out_at * 100}%`, top: `${(y(ceiling) / H) * 100}%`,
                         background: 'var(--negative)' }} aria-hidden />
        )}
        <span className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2"
              style={{ left: `${todayFrac * 100}%`, top: `${(y(spent) / H) * 100}%`,
                       background: 'var(--accent)', '--tw-ring-color': 'var(--surface)' } as React.CSSProperties}
              aria-hidden />
      </div>

      {/* Axis labels, positioned off the SAME fraction as the plotted points.

          The handoff calls this out because it has already gone wrong twice, here and on the Year
          chart: a `justify-between` row spaces labels at even fractions (0, ½, 1) while the data
          sits at day/total, so on a 31-day cycle the ticks drift by ~4 days and the exhaustion dot
          reads a month late. Same `left: frac × 100%` the two markers above use, so a label cannot
          disagree with the dot it is under. Edges clamp instead of centring, or the first and last
          hang off the card.

          Today is dropped near either edge — at 4% along, "today" and the start date occupy the same
          few pixels and overprint. */}
      {start && data.period_end && (
        <div className="relative mt-1.5 h-4 text-[10px] text-ink-3">
          <span className="absolute left-0 whitespace-nowrap">{dayMon(data.period_start!)}</span>
          {todayFrac > 0.12 && todayFrac < 0.88 && (
            <span className="absolute -translate-x-1/2 whitespace-nowrap font-medium text-ink-2"
                  style={{ left: `${todayFrac * 100}%` }}>today</span>
          )}
          <span className="absolute right-0 whitespace-nowrap">{dayMon(data.period_end)}</span>
        </div>
      )}

      {/* The three facts, spelled out — the chart shows the shape, these name it. */}
      <div className="mt-4 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3">
        <div className="bg-surface px-3 py-2.5">
          <div className="tracked text-[10px] text-ink-3">
            {ahead >= 0 ? 'Ahead of pace by' : 'Behind pace by'}
          </div>
          <div className={`money mt-0.5 text-sm font-bold ${ahead >= 0 ? 'text-warn' : 'text-accent'}`}>
            {formatINR(Math.abs(ahead))}
          </div>
          <div className="money mt-0.5 text-[11px] text-ink-3">
            {shortINR(spent)} vs {shortINR(proj.on_pace_inr)}
          </div>
        </div>
        <div className="bg-surface px-3 py-2.5">
          <div className="tracked text-[10px] text-ink-3">Lands at</div>
          <div className={`money mt-0.5 text-sm font-bold ${proj.over_inr > 0 ? 'text-negative' : 'text-accent'}`}>
            {shortINR(proj.projected_inr)}
          </div>
          <div className="money mt-0.5 text-[11px] text-ink-3">
            {proj.over_inr > 0 ? `${shortINR(proj.over_inr)} over` : `${shortINR(ceiling - proj.projected_inr)} spare`}
          </div>
        </div>
        <div className="bg-surface px-3 py-2.5">
          <div className="tracked text-[10px] text-ink-3">Budget runs out</div>
          {proj.runs_out_on ? (
            <>
              <div className="mt-0.5 text-sm font-bold text-negative">{dayMon(proj.runs_out_on)}</div>
              <div className="mt-0.5 text-[11px] text-ink-3">
                <span className="money">{proj.days_before_end}</span> days before the cycle ends
              </div>
            </>
          ) : (
            <>
              <div className="mt-0.5 text-sm font-bold text-accent">Not this cycle</div>
              <div className="mt-0.5 text-[11px] text-ink-3">the pace holds to the end</div>
            </>
          )}
        </div>
      </div>
    </Card>
  )
}
