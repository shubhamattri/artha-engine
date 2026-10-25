import { type ReactNode } from 'react'
import { type UseQueryResult } from '@tanstack/react-query'

import { Card } from './Card'
import Icon from './Icon'
import { EmptyState, ErrorState, Loading } from './States'

/**
 * The ONE shape every secondary wealth ledger takes: a summary strip that answers the domain's
 * single question, then the list, then the manager that was always there.
 *
 * Four domains, one pattern — built once here so Assets, Gold, Insurance and Disposals cannot
 * drift into four different screens. A ledger composes three pieces:
 *   <LedgerBlock>    the four data states, so a failed fetch can never hide behind a spinner
 *   <LedgerSummary>  headline figure, optional two-cell pair, optional split bar, optional note
 *   <LedgerRows>     the list, optionally grouped, one row per holding
 *
 * Every figure arrives preformatted from the caller: this file owns layout and the mono/tabular
 * face, never a currency decision.
 */

export type LedgerTone = 'ink' | 'accent' | 'positive' | 'negative' | 'warn'

const TEXT: Record<LedgerTone, string> = {
  ink: 'text-ink',
  accent: 'text-accent',
  positive: 'text-positive',
  negative: 'text-negative',
  warn: 'text-warn',
}
const FILL: Record<LedgerTone, string> = {
  ink: 'bg-ink',
  accent: 'bg-accent',
  positive: 'bg-positive',
  negative: 'bg-negative',
  warn: 'bg-warn',
}

/** Loading / error / empty / populated. Never `if (isLoading || !data)`, which swallows the error. */
export function LedgerBlock<T>({
  q,
  isEmpty,
  emptyTitle,
  emptyHint,
  children,
}: {
  q: UseQueryResult<T>
  /** true when the fetch succeeded but there is nothing to summarise yet */
  isEmpty?: (data: T) => boolean
  emptyTitle: string
  emptyHint?: string
  children: (data: T) => ReactNode
}) {
  if (q.isError) return <ErrorState message={(q.error as Error)?.message} onRetry={() => q.refetch()} />
  if (!q.data) return <Loading rows={2} />
  if (isEmpty?.(q.data)) return <EmptyState title={emptyTitle} hint={emptyHint} />
  return <>{children(q.data)}</>
}

export type LedgerPair = { label: string; value: ReactNode; tone?: LedgerTone }
export type LedgerSegment = { label: string; value: ReactNode; weight: number; tone: LedgerTone }

/**
 * The summary strip. `value` is the domain's answer at a glance; `pairs` and `split` are absorbed
 * into the same surface when there is a headline, and stand alone when there is not.
 */
export function LedgerSummary({
  overline,
  value,
  caption,
  tone = 'ink',
  pairs,
  split,
  note,
}: {
  overline?: string
  /** preformatted headline figure — rendered mono + tabular */
  value?: string
  caption?: ReactNode
  tone?: LedgerTone
  /** a two-cell hairline grid under the headline */
  pairs?: LedgerPair[]
  /** proportion bar + a legend row per segment */
  split?: LedgerSegment[]
  /** the sentence that stops the headline being misread */
  note?: ReactNode
}) {
  const hasHead = Boolean(overline || value || caption)
  const pairGrid = pairs && pairs.length > 0 && (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-tile border border-line bg-line">
      {pairs.map((p) => (
        <div key={p.label} className="bg-surface-2 p-4">
          <div className="tracked text-[10px] font-semibold text-ink-3">{p.label}</div>
          <div className={`money mt-1 text-lg font-bold leading-tight ${TEXT[p.tone ?? 'ink']}`}>{p.value}</div>
        </div>
      ))}
    </div>
  )
  const total = (split ?? []).reduce((s, x) => s + Math.max(0, x.weight), 0)
  const splitBlock = split && split.length > 0 && (
    <div>
      {total > 0 && (
        <div className="flex h-2 gap-px overflow-hidden rounded-chip">
          {split.map((s) => (
            <div key={s.label} className={FILL[s.tone]} style={{ flex: `${Math.max(0, s.weight)} 1 0` }} />
          ))}
        </div>
      )}
      <div className="mt-2">
        {split.map((s, i) => (
          <div
            key={s.label}
            className={`flex min-h-11 items-center gap-3 ${i > 0 ? 'border-t border-line' : ''}`}
          >
            <span className={`h-2 w-2 shrink-0 rounded-chip ${FILL[s.tone]}`} />
            <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{s.label}</span>
            <span className="money shrink-0 text-sm font-semibold text-ink">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  )

  return (
    <section className="space-y-4">
      {hasHead ? (
        <Card className="space-y-4">
          <div>
            {overline && <div className="tracked text-[11px] font-semibold text-ink-3">{overline}</div>}
            {value && (
              <div className={`money mt-2 text-3xl font-bold leading-none ${TEXT[tone]}`}>{value}</div>
            )}
            {caption && <p className="mt-2 text-sm leading-relaxed text-ink-2">{caption}</p>}
          </div>
          {pairGrid}
          {splitBlock}
        </Card>
      ) : (
        <>
          {caption && <p className="text-sm leading-relaxed text-ink-2">{caption}</p>}
          {pairGrid}
          {splitBlock && <Card>{splitBlock}</Card>}
        </>
      )}
      {note && (
        <div className="flex items-start gap-2 rounded-tile border border-line bg-surface-2 p-4">
          <span className="mt-px flex shrink-0 text-ink-3">
            <Icon name="info" size={16} />
          </span>
          <p className="text-[13px] leading-relaxed text-ink-2">{note}</p>
        </div>
      )}
    </section>
  )
}

export type LedgerRowItem = {
  key: string
  title: string
  /** the one line that says what this holding is — bought year, rate, purity, holding period */
  qualifier?: ReactNode
  qualifierTone?: LedgerTone
  /** preformatted primary figure */
  value: string
  /** preformatted second figure under it — grams, gain, units */
  secondary?: string
  secondaryTone?: LedgerTone
}
export type LedgerGroup = { key: string; label?: string; count?: number; rows: LedgerRowItem[] }

/** The list. One surface, hairline-divided, grouped only when the domain has groups. */
export function LedgerRows({ groups }: { groups: LedgerGroup[] }) {
  return (
    <Card padded={false}>
      {groups.map((g, gi) => (
        <div key={g.key}>
          {g.label && (
            <div
              className={`tracked bg-surface-2 px-4 py-2 text-[10px] font-semibold text-ink-3 ${
                gi > 0 ? 'border-y border-line' : 'border-b border-line'
              }`}
            >
              {g.label}
              {g.count != null && <> · <span className="money">{g.count}</span></>}
            </div>
          )}
          {g.rows.map((r, ri) => (
            <div
              key={r.key}
              className={`flex min-h-14 items-center gap-3 px-4 py-2 ${
                ri > 0 || (!g.label && gi > 0) ? 'border-t border-line' : ''
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-ink">{r.title}</div>
                {r.qualifier && (
                  <div className={`mt-1 truncate text-xs ${r.qualifierTone ? TEXT[r.qualifierTone] : 'text-ink-3'}`}>
                    {r.qualifier}
                  </div>
                )}
              </div>
              <div className="shrink-0 text-right">
                <div className="money text-sm font-semibold text-ink">{r.value}</div>
                {r.secondary && (
                  <div className={`money mt-1 text-xs font-medium ${r.secondaryTone ? TEXT[r.secondaryTone] : 'text-ink-3'}`}>
                    {r.secondary}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}
    </Card>
  )
}
