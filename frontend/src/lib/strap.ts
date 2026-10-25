/**
 * The segmented allocation bar's arithmetic — the reason it "wouldn't code properly".
 *
 * The bug was never CSS. Insurance is Rs 2,500 of Rs 5,00,000 = 0.5%, which on a 307px bar is 1.5px:
 * sub-pixel, rounded away. The legend listed five items and the bar drew four, so it read as broken
 * data. `flex: N 1 0` made it worse by letting segments shrink below even that.
 *
 * Floor every non-zero segment to a visible minimum, then pay for the lift out of the segments that
 * have headroom above the floor, in proportion to how much headroom each has. Widths still total 100,
 * so the bar never overflows and no segment with real money in it disappears.
 */
export const MIN_PCT = 2

export function strapWidths(values: number[], minPct = MIN_PCT): number[] {
  const total = values.reduce((a, v) => a + v, 0)
  // Guarded FIRST and deliberately: without this, an all-zero set divides by zero and renders as one
  // 100%-wide segment — a full bar claiming an allocation that does not exist.
  if (total <= 0) return values.map(() => 0)

  const raw = values.map((v) => (v / total) * 100)
  const lifted = raw.map((p) => (p > 0 && p < minPct ? minPct : p))
  const debt = lifted.reduce((a, p) => a + p, 0) - 100
  if (debt <= 0) return lifted

  // Take the excess only from segments ABOVE the floor. Spreading it evenly would push a segment that
  // was already at the floor back under it, which is the bug this function exists to fix.
  const headroom = lifted.map((p) => (p > minPct ? p - minPct : 0))
  const pool = headroom.reduce((a, h) => a + h, 0)
  if (pool <= 0) return lifted

  return lifted.map((p, i) => p - debt * (headroom[i] / pool))
}

/**
 * Category colours, rotated BY INDEX.
 *
 * Never keyed on a category name: a name-keyed map has to be edited every time a household invents a
 * category, and it silently falls back to grey for the ones nobody thought of.
 *
 * Two deliberate absences. `--negative` #B94F39 is not here — red only ever means money going the
 * wrong way, and a category is not a direction. Plum #7A5C82 is dropped entirely; no purple.
 */
export const STRAP_PALETTE = [
  '#0F7A52', // green (accent)
  '#2E728A', // teal
  '#B07D1E', // gold
  '#A6704A', // clay
  '#6E8B5A', // sage
  '#5B6472', // slate
  '#C9A24B', // sand
] as const

export function strapColor(i: number): string {
  return STRAP_PALETTE[i % STRAP_PALETTE.length]
}

/** Six segments, then a remainder. Past six, adjacent slices are too thin to tell apart. */
export const MAX_SEGMENTS = 6

export type StrapItem = { label: string; value: number }

/**
 * Largest first, always, and everything past the sixth folded into one "+N more".
 *
 * The order is not cosmetic: the bar and the legend beneath it have to read in the same direction or
 * the reader has to map between them, and sorting by value is the only order that is stable as the
 * numbers move.
 */
export function strapSegments(items: StrapItem[], max = MAX_SEGMENTS): {
  segments: StrapItem[]
  hidden: number
  hiddenValue: number
} {
  const sorted = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value)
  if (sorted.length <= max) return { segments: sorted, hidden: 0, hiddenValue: 0 }
  const head = sorted.slice(0, max - 1)
  const tail = sorted.slice(max - 1)
  const hiddenValue = tail.reduce((a, i) => a + i.value, 0)
  return {
    segments: [...head, { label: `+${tail.length} more`, value: hiddenValue }],
    hidden: tail.length,
    hiddenValue,
  }
}
