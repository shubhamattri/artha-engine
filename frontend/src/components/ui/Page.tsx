import { type ReactNode } from 'react'

import { PageDepth, usePageDepth } from './PageDepth'

/**
 * PageContainer — the ONE page wrapper. Every screen starts with this.
 *
 * It owns: the content max-width (1200px, matching the Layout shell band), fluid side gutters
 * (`.page-x` → clamp 1.25rem…4.5rem), fluid top/bottom padding (`.page-y`), and a consistent
 * vertical rhythm between sections. This is what kills the "narrow phone column stranded in the
 * middle of a 1440px desktop" — content fills a real, intentional width on every breakpoint.
 *
 * NESTED, it keeps only the rhythm. Four tabs now compose screens that were each written as their
 * own page, and a page container inside a page container is always a mistake: doubled gutters and
 * doubled top padding. Collapsing to the `gap` alone means a composed tab lays out as one page while
 * each part keeps the spacing it was written with.
 */
export function PageContainer({
  children,
  className = '',
  gap = 'space-y-8',
}: {
  children: ReactNode
  className?: string
  /** vertical rhythm between top-level sections (default comfortable) */
  gap?: string
}) {
  const depth = usePageDepth()
  const chrome = depth === 0 ? 'page-x page-y mx-auto w-full max-w-content' : ''
  return (
    <PageDepth.Provider value={depth + 1}>
      <div className={`${chrome} ${gap} ${className}`.trim()}>{children}</div>
    </PageDepth.Provider>
  )
}

type Cols = 1 | 2 | 3 | 'auto'

/**
 * PageGrid — the responsive column convention every screen follows so the desktop width is USED:
 *   mobile 1-col → md 2-col → xl 3-col (where it fits).
 *
 * - `cols={2}`  → 1 col on phone, 2 from md
 * - `cols={3}`  → 1 → 2 (md) → 3 (xl)
 * - `cols="auto"` → reflows by available width (auto-fit, `min` controls the smallest cell)
 *
 * For equal-weight numeric tiles use <StatGrid> instead (tighter auto-fit).
 */
export function PageGrid({
  cols = 2,
  min = '17rem',
  gap = 'gap-5',
  className = '',
  children,
}: {
  cols?: Cols
  /** only for cols="auto" — smallest a cell may get before it reflows */
  min?: string
  gap?: string
  className?: string
  children: ReactNode
}) {
  if (cols === 'auto') {
    return (
      <div className={`auto-grid ${gap} ${className}`} style={{ ['--min' as string]: min }}>
        {children}
      </div>
    )
  }
  const map: Record<1 | 2 | 3, string> = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3',
  }
  return <div className={`grid ${map[cols]} ${gap} ${className}`}>{children}</div>
}
