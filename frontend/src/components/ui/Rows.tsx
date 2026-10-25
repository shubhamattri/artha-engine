import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { formatINR } from '../../api/client'
import Icon, { type IconName } from './Icon'

type Tone = 'default' | 'positive' | 'negative' | 'accent'
const TONE: Record<Tone, string> = {
  default: 'text-ink',
  positive: 'text-positive',
  negative: 'text-negative',
  accent: 'text-accent',
}

/**
 * Chip — small rounded category/label pill. Category colours come from the caller (data-driven,
 * cat-1…cat-8); NEVER hardcode a category name here. Red is reserved for `negative` only.
 */
export function Chip({
  children,
  color,
  className = '',
}: {
  children: ReactNode
  /** any css color (e.g. a cat-N token value). Falls back to a neutral wash. */
  color?: string
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-chip px-2 py-0.5 text-[11px] font-semibold ${className}`}
      style={
        color
          ? { color, background: `color-mix(in srgb, ${color} 12%, transparent)` }
          : { color: 'var(--ink-2)', background: 'var(--surface-2)' }
      }
    >
      {children}
    </span>
  )
}

/**
 * TxnRow — the compact ONE-LINE transaction row. Left: description (truncates) + meta (date · card ·
 * category chip) directly beneath it. Right: the amount, right-aligned in a mono/tabular column that
 * lines up row-to-row (fixed min-width + `text-right`, never truncated). Use for transaction lists
 * instead of a wide 6-column table. Full-bleed within its parent Card; separated by a hairline.
 */
export function TxnRow({
  description,
  meta,
  amount,
  tone = 'default',
  chip,
  /** The 3px x 32px category pill the ledger spec puts INSIDE the row. A colour here replaces the
   *  chip: a category is a property of the transaction, not a badge stuck to it, and a coloured card
   *  BORDER — which is what this used to drift towards — reads as a state rather than a taxonomy. */
  accent,
  onClick,
}: {
  description: string
  meta?: ReactNode
  amount: number | string
  tone?: 'default' | 'positive' | 'negative' | 'accent'
  chip?: ReactNode
  accent?: string
  onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={`flex items-center gap-3 border-b border-line last:border-0 ${
        onClick ? 'cursor-pointer transition-colors hover:bg-surface-2' : ''}`}
      style={{ minHeight: accent ? 60 : undefined, padding: accent ? '10px 16px' : '12px 4px' }}
    >
      {accent && (
        <span className="flex-none rounded-full" style={{ width: 3, height: 32, background: accent }} />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-ink">{description}</div>
        {meta && <div className="mt-0.5 truncate text-[11px] text-ink-3">{meta}</div>}
      </div>
      {chip}
      <div className={`money shrink-0 min-w-[5rem] text-right font-semibold tabular-nums ${TONE[tone]}`}
           style={{ fontSize: accent ? 15 : 14 }}>
        {typeof amount === 'number' ? formatINR(amount) : amount}
      </div>
    </div>
  )
}

export function CompactRow({
  label,
  dateChip,
  amount,
  tone = 'default',
  onClick,
}: {
  label: ReactNode
  /** small right-aligned chip, e.g. a due-day "~5th" or a date */
  dateChip?: ReactNode
  amount: number | string
  tone?: Tone
  onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={`flex items-center gap-2 py-1 text-[13px] ${
        onClick ? 'cursor-pointer transition-colors hover:text-ink' : ''
      }`}
    >
      <span className="min-w-0 flex-1 truncate text-ink-2">{label}</span>
      {dateChip != null && (
        <span className="shrink-0 rounded-chip bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-ink-3">
          {dateChip}
        </span>
      )}
      <span className={`money shrink-0 min-w-[4.5rem] text-right text-sm font-medium tabular-nums ${TONE[tone]}`}>
        {typeof amount === 'number' ? formatINR(amount) : amount}
      </span>
    </div>
  )
}

/**
 * DetailCell — the small label-over-value pair used inside an expanded record (a loan's terms, a
 * sale's numbers). Not a form Field: read-only, mono value, optional accent for the figure that
 * matters. Put several in a grid to make a detail panel.
 */
export function DetailCell({
  label,
  value,
  accent,
  hint,
}: {
  label: string
  value: ReactNode
  accent?: boolean
  /** one line under the value explaining what it does — e.g. why a number is not a tax number */
  hint?: string
}) {
  return (
    <div>
      <div className="text-[11px] font-medium text-ink-3">{label}</div>
      <div className={`money mt-0.5 text-sm font-medium ${accent ? 'text-accent' : 'text-ink'}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] leading-snug text-ink-3">{hint}</div>}
    </div>
  )
}

export type RecordField = {
  label: string
  value: ReactNode
  /** render the value as mono/tabular (money, counts) */
  mono?: boolean
  tone?: Tone
}

/**
 * RecordRow — the responsive row that EVERY wide manager table becomes (Cashflow, Sinking,
 * BudgetPlan, Recurring, Debt amortisation/disbursement…). No horizontal scroll, no truncated
 * figures. Left: title (truncates) + muted subtitle. Right/below: the fields in FIXED, right-aligned
 * slots (each `w-28`) so the figure columns line up row-to-row instead of the old ragged left-flow
 * wrap. Labels + values are right-aligned and tabular; money uses the mono `.money` face. On phones
 * the slots reflow two-up; from `sm` they sit in one right-hanging band beneath the title.
 *
 * This is the component-level answer to the `.rtable` CSS hack — but usable from JSX directly.
 * (For nested/expanded one-liners use CompactRow instead of a whole RecordRow.)
 */
export function RecordRow({
  title,
  subtitle,
  fields,
  actions,
  onClick,
}: {
  title: ReactNode
  subtitle?: ReactNode
  fields: RecordField[]
  actions?: ReactNode
  onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={`rounded-2xl border border-line bg-surface p-4 ${
        onClick ? 'cursor-pointer transition-colors hover:border-line-2' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-ink">{title}</div>
          {subtitle && <div className="mt-0.5 truncate text-xs text-ink-3">{subtitle}</div>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </div>
      {fields.length > 0 && (
        // Strict aligned grid: on phones the fields sit in an even 2-column grid (no ragged flex-wrap
        // where the last odd field floats mid-row); from `sm` they collapse into one right-hanging band
        // of fixed slots that line up card-to-card.
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:flex sm:flex-wrap sm:justify-end sm:gap-x-6">
          {fields.map((f, i) => (
            <div key={i} className="min-w-0 text-right sm:w-24">
              <div className="truncate text-[11px] font-medium text-ink-3">{f.label}</div>
              <div
                className={`mt-0.5 truncate text-sm font-medium tabular-nums ${f.mono ? 'money' : ''} ${
                  TONE[f.tone ?? 'default']
                }`}
              >
                {f.value}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * LedgerRow — the "open a ledger to add or correct" row, in one place.
 *
 * Three screens hand-rolled the same thing with three different heights (Plan's new row, Savings' six
 * ledgers, Balance's tools) and they had already drifted: 52 vs 56 px, bubble vs no bubble, chevron vs
 * arrow. It is the row the whole no-forms-on-a-tab rule depends on — every manager that came off a tab
 * surface is now reached through one of these — so it is worth having exactly once.
 *
 * The defaults ARE the handoff's spec: min-height 52, padding 8/16, gap 12, label 14/600, value 14/600
 * mono tabular, 16px chevron in ink-3, hairline above every row but the first, surface-2 on hover.
 *
 * `icon` and `sub` are optional because Savings' six rows carry a count and a domain glyph that the
 * spec's simpler row does not have, and dropping them to match a mock would delete information the
 * household uses. Spec-exact unless a caller has a reason.
 */
export function LedgerRow({
  to, label, value, sub, icon, tone, first = false, away = false, onClick,
}: {
  to?: string
  label: string
  value?: string
  /** ReactNode, not string: Savings' sub-lines carry a formatted count with its own tone. */
  sub?: ReactNode
  icon?: IconName
  /** Tailwind text colour for the value. Default ink. */
  tone?: string
  first?: boolean
  /** true → an arrow, for a row that leaves this area rather than descending into it */
  away?: boolean
  onClick?: () => void
}) {
  const inner = (
    <>
      {icon && (
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-chip bg-accent-soft text-accent">
          <Icon name={icon} size={16} />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-ink">{label}</span>
        {sub && <span className="mt-0.5 block truncate text-xs text-ink-3">{sub}</span>}
      </span>
      {value && (
        // 66px and nowrap: sized for a lakh string, "₹1.25 Cr" wrapped onto two lines.
        <span className={`money shrink-0 whitespace-nowrap text-right text-sm font-semibold tabular-nums ${tone ?? 'text-ink'}`}
              style={{ minWidth: 66 }}>
          {value}
        </span>
      )}
      <span className="flex shrink-0 text-ink-3">
        <Icon name={away ? 'arrow' : 'chevron'} size={16} />
      </span>
    </>
  )
  const cls = `flex items-center gap-3 text-ink transition-colors hover:bg-surface-2 ${
    first ? '' : 'border-t border-line'}`
  const style = { minHeight: 52, padding: '8px 16px' }
  if (to) return <Link to={to} className={cls} style={style}>{inner}</Link>
  return <button onClick={onClick} className={`${cls} w-full text-left`} style={style}>{inner}</button>
}

/** The card the rows live in: surface, hairline, radius 16, the system shadow, clipped. */
export function LedgerList({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-surface">{children}</div>
  )
}
