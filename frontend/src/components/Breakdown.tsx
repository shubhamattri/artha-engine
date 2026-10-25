import { formatINR } from '../api/client'
import InfoTip from './InfoTip'

/**
 * The canonical "total = A + B + C" view: a list of
 * labeled value rows in one consistent grammar, separated from a bold accent Total
 * by a dashed divider. Reused everywhere a number decomposes — never hand-built per page.
 *
 * Row grammar: label (muted, left) · optional hint/tip · value (tabular, right).
 * `sign` flips a value's display (+/−) without changing the math the caller passes in.
 */
export type BreakdownRow = {
  label: string
  value: number
  hint?: string
  tip?: string
  sign?: '+' | '−'
  accent?: 'positive' | 'negative' | 'muted'
}

const VAL = { positive: 'text-positive', negative: 'text-negative', muted: 'text-ink-2' } as const

function Row({ r }: { r: BreakdownRow }) {
  return (
    <div className="flex items-baseline justify-between py-2">
      <span className="text-sm text-ink-2">
        {r.label}
        {r.tip && <InfoTip text={r.tip} />}
        {r.hint && <span className="ml-2 text-[10px] tracked text-ink-3">{r.hint}</span>}
      </span>
      <span className={`money text-sm ${r.accent ? VAL[r.accent] : 'text-ink'}`}>
        {r.sign ?? ''}{formatINR(r.value)}
      </span>
    </div>
  )
}

export default function Breakdown({
  rows,
  total,
}: {
  rows: BreakdownRow[]
  total: { label: string; value: number; tip?: string; accent?: 'accent' | 'negative' }
}) {
  const totalColor = total.accent === 'negative' ? 'text-negative' : 'text-accent'
  return (
    <div className="rounded-card border border-line bg-surface p-6">
      {rows.map((r) => (
        <Row key={r.label} r={r} />
      ))}
      <div className="hr-dashed my-1" />
      <div className="flex items-baseline justify-between pt-2">
        <span className="text-sm font-medium text-ink">
          {total.label}
          {total.tip && <InfoTip text={total.tip} />}
        </span>
        <span className={`money text-lg ${totalColor}`}>{formatINR(total.value)}</span>
      </div>
    </div>
  )
}
