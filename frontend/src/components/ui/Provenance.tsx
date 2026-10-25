/**
 * Where a number came from, as a badge — the app's honesty primitive.
 *
 * Four claims that must never render alike, because the difference between them is the difference
 * between a calculation and a guess:
 *
 *   Measured by Artha                    derived from this household's own holdings
 *   Measured elsewhere — entered by hand read off a broker screen by a person, + the date it is true to
 *   Assumed                              a stated rate, not a measurement of anything
 *   Stale                                measured, but past the age at which it can be trusted
 *
 * A hand-entered figure that reads as app-derived is exactly the laundering the Windfall page exists
 * to prevent, so "Assumed" carries a DASHED border — the same visual grammar the uncertain panel uses —
 * while the two measured tags are solid. Grey rather than amber for the hand-entered one: it is a fact
 * about provenance, not a fault.
 *
 * Copy is verbatim from the handoff. Literal spec: 9px/700, letter-spacing .12em, uppercase,
 * padding 2px 6px, fully rounded.
 */
export type ProvenanceKind = 'derived' | 'by_hand' | 'assumed' | 'stale'

const TONE: Record<ProvenanceKind, { cls: string; label: string }> = {
  derived: { cls: 'bg-accent-soft text-accent', label: 'Measured by Artha' },
  by_hand: { cls: 'border border-line-2 bg-surface text-ink-2', label: 'Measured elsewhere — entered by hand' },
  assumed: { cls: 'border border-dashed border-line-2 text-ink-2', label: 'Assumed' },
  stale: { cls: 'bg-warn/10 text-warn', label: 'Stale' },
}

export function ProvenanceBadge({ kind, label }: { kind: ProvenanceKind; label?: string }) {
  const t = TONE[kind]
  return (
    <span className={`rounded-chip font-bold uppercase ${t.cls}`}
          style={{ fontSize: 9, fontWeight: 700, letterSpacing: '.12em', padding: '2px 6px' }}>
      {label ?? t.label}
    </span>
  )
}

/**
 * The whole provenance statement for one rate, badge plus the as-of date a hand-entered figure needs.
 * A measured-elsewhere tag without its date is only half a claim — it says a person read it, not when.
 */
export function Provenance({
  source, measuredBy, asOf, stale,
}: {
  source: 'observed' | 'assumed'
  measuredBy?: 'derived_in_app' | 'entered_by_hand' | null
  asOf?: string | null
  stale?: boolean
}) {
  if (source !== 'observed') return <ProvenanceBadge kind="assumed" />
  const byHand = measuredBy === 'entered_by_hand'
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <ProvenanceBadge kind={byHand ? 'by_hand' : 'derived'} />
      {byHand && asOf && (
        <span className="money text-[10px] font-semibold text-ink-3">as of {asOf}</span>
      )}
      {stale && <ProvenanceBadge kind="stale" />}
    </span>
  )
}
