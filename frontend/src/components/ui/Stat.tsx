import { Children } from 'react'

import { formatINR } from '../../api/client'

type Accent = 'ink' | 'accent' | 'positive' | 'negative'
const COLOR: Record<Accent, string> = {
  ink: 'text-ink', accent: 'text-accent', positive: 'text-positive', negative: 'text-negative',
}

/** Money value as mono + tabular figures (the core anti-slop rule). */
export function Money({ value, className = '' }: { value: number; className?: string }) {
  return <span className={`money ${className}`}>{formatINR(value)}</span>
}

/**
 * One canonical stat tile — replaces the four ad-hoc `Stat` copies across the app.
 * `value` may be a preformatted string (e.g. "18%") or a number (rendered in the household's currency).
 */
export function Stat({
  label, value, sub, accent = 'ink', onClick,
}: {
  label: string
  value: string | number
  sub?: string
  accent?: Accent
  onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={`bg-surface border border-line rounded-2xl p-4 ${onClick ? 'cursor-pointer hover:border-line-2' : ''}`}
    >
      <div className="text-xs font-semibold text-ink-3">{label}</div>
      <div className={`money mt-1.5 text-xl font-bold ${COLOR[accent]}`}>
        {typeof value === 'number' ? formatINR(value) : value}
      </div>
      {sub && <div className="mt-1 text-xs text-ink-2">{sub}</div>}
    </div>
  )
}

/** Responsive grid of equal-weight tiles (auto-fit, never narrower than --min). */
/**
 * A group of stats is ONE surface divided by hairlines — not N floating boxes.
 *
 * This was `auto-grid gap-3` holding individually bordered, individually shadowed, individually
 * rounded cards. Repeated on nine screens (42 call sites) it became the app's only compositional
 * idea, and a grid of identical rounded boxes is the single most recognisable "generated" look.
 *
 * The frame lives here: one border, one radius, one surface. `gap-px` over a `bg-line` background
 * draws the dividers, which is wrap-proof — cells that reflow onto a second row get their hairlines
 * for free, where `divide-x` would not. StatCard is now just a cell and carries no chrome.
 */
export function StatGrid({ min = '9rem', children }: { min?: string; children: React.ReactNode }) {
  // The column count must DIVIDE the number of stats. With the hairline background behind the cells,
  // a leftover grid cell is no longer invisible — it renders as a grey slab. Commitments has three
  // stats, auto-fit chose two columns, and the empty fourth cell showed up as exactly that. auto-fit
  // cannot know its child count; this can, so the count picks the columns.
  // `min` is kept for call-site compatibility and no longer drives layout.
  void min
  const n = Children.count(children)
  const COLS: Record<number, string> = {
    1: 'grid-cols-1',
    2: 'grid-cols-2',
    3: 'grid-cols-1 sm:grid-cols-3',
    4: 'grid-cols-2 sm:grid-cols-4',
    5: 'grid-cols-1 sm:grid-cols-5',
    6: 'grid-cols-2 sm:grid-cols-3',
  }
  return (
    <div className={`grid gap-px overflow-hidden rounded-2xl border border-line bg-line ${COLS[n] ?? 'grid-cols-2'}`}>
      {children}
    </div>
  )
}

