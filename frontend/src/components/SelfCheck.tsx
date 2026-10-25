import { useQuery } from '@tanstack/react-query'

import { formatINR } from '../api/client'
import { fetchConsistency, type ConsistencyFinding } from '../api/meta'
import { Card, ErrorState, Loading, SectionHeader } from './ui'

/**
 * Artha checking its own arithmetic, in front of the person who relies on it.
 *
 * Every module here was built in its own tab, each correct alone, and they have still contradicted
 * each other — a redefined field once put −22,500 on the main screen, and two asset queries once
 * disagreed by ₹6,50,000. These assertions are what catch that.
 *
 * They already ran before and after every deploy. What they could not do was tell the person
 * looking at a wrong number that it was wrong, which is the only moment it matters.
 */

const TONE = {
  error: { dot: 'bg-negative', label: 'text-negative', word: 'Wrong' },
  warn: { dot: 'bg-warn', label: 'text-warn', word: 'Worth knowing' },
  ok: { dot: 'bg-accent', label: 'text-ink-3', word: 'Agrees' },
} as const

function Finding({ f }: { f: ConsistencyFinding }) {
  const tone = TONE[f.severity] ?? TONE.ok
  return (
    <div className="flex gap-2.5 border-b border-line py-2.5 last:border-0">
      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] leading-relaxed text-ink-2">{f.detail}</p>
        {f.expected != null && f.actual != null && (
          <p className="mt-0.5 text-[11.5px] text-ink-3">
            expected {formatINR(f.expected)} · actual {formatINR(f.actual)}
            {f.gap != null && <> · off by {formatINR(Math.abs(f.gap))}</>}
          </p>
        )}
        {/* Only on failures. The fix names the function that owns the number, which is the one
            thing that turns "a figure is wrong" into something anyone can act on. */}
        {f.severity === 'error' && f.fix && (
          <p className="mt-1 rounded-input bg-surface-2 px-2 py-1 text-[11px] leading-relaxed text-ink-3">
            {f.fix}
          </p>
        )}
        <p className="mt-0.5 text-[10.5px] text-ink-3">{f.owner}</p>
      </div>
    </div>
  )
}

export default function SelfCheck() {
  // Not on an interval. This runs real queries across every module, and it is a thing you open when
  // a number looks wrong, not something that should be recomputing behind a screen nobody is on.
  const q = useQuery({ queryKey: ['consistency'], queryFn: fetchConsistency, staleTime: 60_000 })

  const findings = q.data?.findings ?? []
  const errors = findings.filter((f) => f.severity === 'error')
  const warns = findings.filter((f) => f.severity === 'warn')
  const oks = findings.filter((f) => f.severity === 'ok')

  return (
    <Card>
      <SectionHeader
        level={2}
        title="Does everything agree?"
        sub="Each part of Artha computes its own numbers. This checks that they still match each other."
      />

      {q.isLoading && <Loading rows={3} />}
      {q.isError && <ErrorState message="Couldn’t run the checks." onRetry={() => q.refetch()} />}

      {q.data && (
        <>
          <p className="mb-2 text-[13px] font-medium text-ink">
            {q.data.ok
              ? `All ${oks.length} checks agree${warns.length ? `, with ${warns.length} worth knowing about` : ''}.`
              : `${errors.length} figure${errors.length === 1 ? '' : 's'} disagree — treat ${errors.length === 1 ? 'it' : 'them'} as wrong until fixed.`}
          </p>

          {/* Failures first, then the notes, then the ones that pass. A screen that leads with
              nineteen greens buries the one red that is the reason to be here. */}
          {[...errors, ...warns].map((f) => <Finding key={f.check} f={f} />)}

          {oks.length > 0 && (
            <details className="mt-2">
              <summary className="min-h-11 cursor-pointer list-none py-2 text-[11.5px] font-semibold text-ink-2">
                {oks.length} checks that agree ▸
              </summary>
              {oks.map((f) => <Finding key={f.check} f={f} />)}
            </details>
          )}
        </>
      )}
    </Card>
  )
}
