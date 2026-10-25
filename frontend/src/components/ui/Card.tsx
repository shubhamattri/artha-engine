import { type ReactNode } from 'react'

import { usePageDepth } from './PageDepth'

/**
 * Card — the ONE generic surface. Rounded (radius-tile), white, soft shadow (applied centrally in
 * index.css via `.rounded-2xl.bg-surface`). Use for any grouped block: a table wrapper, a chart
 * panel, a form. Replaces ad-hoc `border border-hairline` boxes AND the sharp `bg-void` panels.
 */
export function Card({
  children,
  className = '',
  padded = true,
  as: Tag = 'div',
  onClick,
}: {
  children: ReactNode
  className?: string
  /** set false when the child (e.g. a table) manages its own edge padding */
  padded?: boolean
  as?: 'div' | 'section' | 'article'
  onClick?: () => void
}) {
  return (
    <Tag
      onClick={onClick}
      className={`rounded-2xl border border-line bg-surface ${padded ? 'p-4 sm:p-5' : ''} ${
        onClick ? 'cursor-pointer transition-colors hover:border-line-2' : ''
      } ${className}`}
    >
      {children}
    </Tag>
  )
}

/**
 * SectionHeader — the ONE heading token. Fixes the per-page hand-rolled `<h1>` chaos (some pages
 * used `text-2xl font-extrabold`, others a tiny `text-[11px] tracked` label — same fonts, wildly
 * different hierarchy). Every page/section title renders through this: Clash Display (via the
 * global h1/h2 rule), consistent size, optional eyebrow + right-aligned action slot (filters,
 * period toggle, "+ Add").
 */
export function SectionHeader({
  title,
  eyebrow,
  sub,
  action,
  level = 1,
  className = '',
}: {
  title: string
  /** small uppercase label above the title */
  eyebrow?: string
  /** secondary line below the title */
  sub?: string
  /** right-side controls (PeriodToggle, FilterBar trigger, Add button…) */
  action?: ReactNode
  level?: 1 | 2
  className?: string
}) {
  // A composed tab owns the one h1; the screens inside it become sections of that page. Demoting
  // here rather than at each call site is what lets a page be rendered standalone AND as part of a
  // tab without knowing which it is — and it keeps exactly one h1 per screen either way.
  const composed = usePageDepth() > 1
  const effective = composed ? 2 : level
  const Heading = effective === 1 ? 'h1' : 'h2'
  const size = effective === 1 ? 'text-2xl' : 'text-lg'
  return (
    <div className={`flex flex-wrap items-end justify-between gap-3 ${className}`}>
      <div className="min-w-0">
        {eyebrow && <div className="tracked mb-1 text-[11px] text-ink-3">{eyebrow}</div>}
        <Heading className={`${size} font-bold leading-tight text-ink`}>{title}</Heading>
        {sub && <p className="mt-1 text-sm text-ink-2">{sub}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  )
}
