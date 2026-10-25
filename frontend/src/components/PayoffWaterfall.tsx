import { type DebtStep, formatINR, shortINR } from '../api/client'
import { Card } from './ui'

/**
 * The roll-down, as a step chart.
 *
 * This is the Debt tab's actual argument and it was prose spread across seven numbered cards. The
 * argument is one shape: when a loan clears, its EMI does not go back into the household's pocket,
 * it rolls onto the next loan. So the monthly payment STEPS UP each time something closes, and each
 * remaining loan clears faster than the one before it. Seven paragraphs cannot show acceleration;
 * one rising staircase can.
 *
 * The dashed reference is the same plan without the roll-down — spare cash alone, flat forever. The
 * gap between the staircase and that flat line IS the mechanism, and it is the only honest way to
 * show that the final payment is 2.3x the first without anyone finding 2.3x extra money.
 *
 * Every figure comes from /api/debt's `steps`, which is the simulation's own output. Nothing here
 * is recomputed — a chart that re-derives its numbers is a second opinion pretending to be a
 * picture.
 */
const W = 320
const H = 150

export default function PayoffWaterfall({ steps, spare, className = '' }: {
  /** In the simulation's own order: by when each loan closes. */
  steps: DebtStep[]
  /** Spare cash per month, before any EMI is freed. The flat line. */
  spare: number
  className?: string
}) {
  // A step with no closing month never clears inside the horizon, so it has no riser to draw and no
  // dot to place. Dropping it is not hiding it: the loan list below still carries it, and a bar
  // drawn to an x it does not have would be an invention.
  const usable = steps.filter((s) => s.closes_in_months != null && s.monthly_payment > 0)
  if (usable.length < 2) return null

  const months = usable[usable.length - 1].closes_in_months as number
  const maxPay = Math.max(...usable.map((s) => s.monthly_payment))
  // Headroom so the last step is not welded to the top edge. Derived, not the mock's literal 2.8L —
  // a hardcoded ceiling is right for one household and clips the next one's chart.
  const ceiling = Math.max(maxPay * 1.08, spare * 1.2)

  const x = (m: number) => (m / months) * W
  const y = (v: number) => H - (v / ceiling) * H

  // Each level runs from where it becomes reachable to the month its loan closes. Where a level
  // becomes reachable LATER than the previous one closed, the previous level simply extends — the
  // household is not paying the bigger figure yet, and drawing a diagonal across the gap would say
  // it was ramping up when it was holding.
  const pts: { m: number; v: number }[] = []
  usable.forEach((s, i) => {
    const prev = usable[i - 1]
    const start = i === 0 ? 0 : (s.peak_from_month ?? (prev.closes_in_months as number))
    if (i > 0) pts.push({ m: start, v: prev.monthly_payment })
    pts.push({ m: start, v: s.monthly_payment })
    pts.push({ m: s.closes_in_months as number, v: s.monthly_payment })
  })

  const line = pts.map((p) => `${x(p.m).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
  const area = `M0,${H} ${line.split(' ').map((s) => `L${s}`).join(' ')} L${x(months).toFixed(1)},${H} Z`
  const growth = usable[usable.length - 1].monthly_payment / usable[0].monthly_payment

  return (
    <Card className={className}>
      <div className="text-xs font-semibold text-ink-2">What clearing one loan does to the next</div>
      <p className="mt-1 text-[11.5px] leading-relaxed text-ink-2">
        Each time a loan closes its EMI rolls onto the one after it, so the monthly payment steps up
        and every remaining loan clears faster than the last.
      </p>

      {/* Same construction as the Plan's cumulative chart: the LINES stretch (x is time, y is money,
          neither proportion carries meaning) and the DOTS are HTML percentages, because a circle
          under a non-uniform scale renders as an ellipse and vectorEffect corrects the outline, not
          the geometry. */}
      <div className="relative mt-3.5" style={{ height: H }}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
             className="block w-full overflow-visible" style={{ height: H }}
             role="img"
             aria-label={`Monthly payment stepping from ${formatINR(usable[0].monthly_payment)} to ${formatINR(usable[usable.length - 1].monthly_payment)} over ${months} months`}>
          <line x1="0" y1={y(spare)} x2={W} y2={y(spare)} stroke="var(--ink-3)" strokeWidth="1.5"
                strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
          <path d={area} fill="var(--accent)" opacity="0.10" />
          <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="2.5"
                    strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
        {usable.map((s, i) => {
          const last = i === usable.length - 1
          return (
            <span key={s.id}
                  className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ${last ? 'h-3 w-3' : 'h-2 w-2'}`}
                  style={{
                    left: `${((s.closes_in_months as number) / months) * 100}%`,
                    top: `${(y(s.monthly_payment) / H) * 100}%`,
                    background: last ? 'var(--accent-2)' : 'var(--accent)',
                    '--tw-ring-color': 'var(--surface)',
                  } as React.CSSProperties}
                  aria-hidden />
          )
        })}
      </div>

      {/* Axis off the same month/total mapping the points use — never a space-between row, which
          spaces labels at even fractions while the data sits at month/total. */}
      <div className="relative mt-1.5 h-4 text-[10px] text-ink-3">
        <span className="absolute left-0">now</span>
        <span className="absolute right-0 whitespace-nowrap">
          <span className="money">{months}</span> months
        </span>
      </div>

      <p className="mt-3 border-t border-line pt-3 text-[11.5px] leading-relaxed text-ink-2">
        Spare cash alone is the flat line at <span className="money font-semibold text-ink">{shortINR(spare)}</span> a
        month and never moves. The staircase ends at{' '}
        <span className="money font-semibold text-ink">{shortINR(usable[usable.length - 1].monthly_payment)}</span> —{' '}
        <span className="money font-semibold text-ink">{growth.toFixed(1)}×</span> the first payment, out of the
        same income, because {usable.length - 1} EMIs have been freed by then.
      </p>
    </Card>
  )
}
