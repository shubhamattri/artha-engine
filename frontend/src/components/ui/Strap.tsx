import { type ReactNode } from 'react'

import { strapColor, strapSegments, strapWidths, type StrapItem } from '../../lib/strap'

/**
 * The segmented allocation bar, in one place, for all four of its uses: income allocation, Wealth
 * composition, recurring investments, gold pledged-vs-free.
 *
 * Widths come from `strapWidths`, which floors a sub-pixel segment to something visible and pays for
 * the lift out of the segments with headroom. Rendered with an explicit `width: %` and `flex: none` —
 * `flex: N 1 0` lets a segment shrink back below the floor, which is the bug the floor exists to fix.
 *
 * Colour is a decision the CALLER makes, because it depends on what the segments are:
 *   `tone="category"` rotates the palette by index — for things that are different kinds
 *   `tone="state"`    uses the semantic colours the caller passes — for states of ONE thing
 *                     (pledged/free, paid/due, inside/over target), where a rotating palette would
 *                     imply five unrelated categories where there are two states.
 */
export type StrapTone = 'category' | 'state'

export function Strap({
  items,
  height = 14,
  tone = 'category',
  colors,
  loading = false,
  overshootNote,
  className = '',
}: {
  items: StrapItem[]
  /** 12 compact · 14 default · 16 prominent. Gap steps to 3px at 16 so the segments still separate. */
  height?: 12 | 14 | 16
  tone?: StrapTone
  /** tone="state" only — one colour per item, in the item's own order. */
  colors?: string[]
  loading?: boolean
  /** Shown when the parts exceed the whole. The bar stays normalised; the overflow is said in words. */
  overshootNote?: ReactNode
  className?: string
}) {
  const gap = height >= 16 ? 3 : 2
  const track = `flex overflow-hidden rounded-full ${className}`
  const style = { height, gap }

  // Loading holds the loaded geometry — same height, full width, no spinner — so nothing on the page
  // moves when the data lands.
  if (loading) {
    return <div className={track} style={{ ...style, background: 'var(--surface-2)' }} aria-hidden />
  }

  const { segments } = strapSegments(items)
  const total = segments.reduce((a, s) => a + s.value, 0)

  // Empty is a DASHED track, not an empty solid one: a solid grey bar reads as "allocated to something
  // unnamed", and this state means nothing has been allocated at all.
  if (!segments.length || total <= 0) {
    return (
      <div className={`rounded-full border border-dashed border-line-2 ${className}`}
           style={{ height }} role="img" aria-label="Nothing allocated yet" />
    )
  }

  const widths = strapWidths(segments.map((s) => s.value))

  return (
    <div>
      <div className={track} style={style}
           role="img"
           aria-label={segments.map((s, i) => `${s.label} ${widths[i].toFixed(0)}%`).join(', ')}>
        {segments.map((s, i) => (
          <span
            key={`${s.label}-${i}`}
            title={s.label}
            style={{
              width: `${widths[i]}%`,
              flex: 'none',
              background: tone === 'state' ? (colors?.[i] ?? strapColor(i)) : strapColor(i),
            }}
          />
        ))}
      </div>
      {overshootNote && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-negative">{overshootNote}</p>
      )}
    </div>
  )
}

/**
 * The legend, in the strap's own order.
 *
 * Separate from the bar because three of the four uses want a different shape beneath it (a wrapping
 * row here, 52px band rows on Balance), and because a legend that sorts differently from the bar makes
 * the reader map between them.
 */
export function StrapLegend({
  items,
  tone = 'category',
  colors,
  format,
  className = '',
}: {
  items: StrapItem[]
  tone?: StrapTone
  colors?: string[]
  format: (value: number) => string
  className?: string
}) {
  const { segments } = strapSegments(items)
  const widths = strapWidths(segments.map((s) => s.value))
  return (
    <ul className={`flex flex-wrap gap-x-4 gap-y-1.5 ${className}`}>
      {segments.map((s, i) => (
        <li key={`${s.label}-${i}`} className="flex min-w-0 items-center gap-1.5 text-[11px]">
          <span className="h-2 w-2 flex-none rounded-full"
                style={{ background: tone === 'state' ? (colors?.[i] ?? strapColor(i)) : strapColor(i) }} />
          <span className="truncate text-ink-2">{s.label}</span>
          <span className="money font-semibold tabular-nums text-ink">{format(s.value)}</span>
          <span className="money tabular-nums text-ink-3">{widths[i].toFixed(1)}%</span>
        </li>
      ))}
    </ul>
  )
}
