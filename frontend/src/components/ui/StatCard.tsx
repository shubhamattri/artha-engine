import { formatINR } from '../../api/client'
import Icon, { type IconName } from './Icon'

export type StatTone = 'default' | 'accent' | 'positive' | 'negative' | 'warn'

const VALUE_COLOR: Record<StatTone, string> = {
  default: 'text-ink',
  accent: 'text-accent',
  positive: 'text-positive',
  negative: 'text-negative',
  warn: 'text-warn',
}

/**
 * StatCard — the ONE summary tile. Replaces EVERY sharp legacy summary cell
 * (Debt `Summary`, Commitments `Summary`, and the local `Stat` copies in Recurring / Insurance /
 * Sinking managers — all `bg-void px-6 py-5` squares in `gap-px bg-hairline` grids).
 *
 * Rounded (radius-tile), lifted (auto-shadow), figures always mono + tabular via `.money`.
 * `value` may be a number (rendered in the household's currency) or a preformatted string ("18%", "3.2×").
 * `delta` is an optional signed change line; give it its own `deltaTone`.
 *
 * Lay these out in <StatGrid> (auto-fit) or a <PageGrid>.
 */
export function StatCard({
  label,
  value,
  sub,
  tone = 'default',
  icon,
  delta,
  deltaTone = 'positive',
  onClick,
  className = '',
}: {
  label: string
  value: string | number
  sub?: string
  tone?: StatTone
  icon?: IconName
  delta?: string
  deltaTone?: Extract<StatTone, 'positive' | 'negative' | 'warn'>
  onClick?: () => void
  className?: string
}) {
  return (
    <div
      onClick={onClick}
      className={`bg-surface p-4 ${
        onClick ? 'cursor-pointer transition-colors hover:bg-surface-2' : ''
      } ${className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="tracked text-[10px] font-semibold text-ink-3">{label}</div>
        {icon && (
          <span className="flex h-7 w-7 items-center justify-center rounded-chip bg-accent-soft text-accent">
            <Icon name={icon} size={15} />
          </span>
        )}
      </div>
      <div className={`money mt-1.5 text-[22px] font-bold leading-tight ${VALUE_COLOR[tone]}`}>
        {typeof value === 'number' ? formatINR(value) : value}
      </div>
      <div className="mt-1 flex items-center gap-2">
        {delta && (
          <span className={`money text-xs font-semibold ${VALUE_COLOR[deltaTone]}`}>{delta}</span>
        )}
        {sub && <span className="text-xs text-ink-2">{sub}</span>}
      </div>
    </div>
  )
}

// StatGrid lives in ./Stat (single source) — lay StatCards inside <StatGrid> or <PageGrid>.
