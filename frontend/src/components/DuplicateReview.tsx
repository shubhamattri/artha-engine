import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchDuplicateReview, mergeDuplicate, dismissDuplicate, formatINR,
  type DupCandidate, type DupReviewRow,
} from '../api/client'
import { Card, SectionHeader } from './ui'
import { ViewOnly, useCan } from '../auth/Can'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'

/** The ambiguous duplicate queue: same amount, same card, same day, look-alike merchant — but no shared
 *  bank reference, so it can't be decided automatically. You confirm ("Same payment" → merge) or dismiss
 *  ("Keep both" → remembered). Deterministic duplicates auto-merge and never reach here. */
export default function DuplicateReview() {
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('ledger.housekeeping', 'edit')
  const { data } = useQuery({ queryKey: ['duplicate-review'], queryFn: fetchDuplicateReview })
  const candidates = data?.candidates ?? []
  if (candidates.length === 0) return null

  return (
    <Card>
      <SectionHeader level={2} title={`Possible duplicates · ${candidates.length}`}
        sub="Same amount, card and day with a look-alike merchant, but no shared bank reference — so we can't be sure. Confirm the ones that are one payment; keep the rest." />
      {/* Said once at the top rather than on each of up to fifteen rows. */}
      {!mayEdit && <div className="mt-3"><ViewOnly what="possible duplicates" /></div>}
      <div className="mt-3 space-y-2">
        {candidates.slice(0, 15).map((c) => (
          <DupRow key={`${c.primary.id}-${c.duplicate.id}`} c={c} onDone={refresh} />
        ))}
      </div>
    </Card>
  )
}

function Side({ r }: { r: DupReviewRow }) {
  return (
    <div className="min-w-0 flex-1">
      <div className="truncate text-sm font-semibold text-ink">{r.merchant || r.description}</div>
      <div className="mt-0.5 truncate text-[11px] text-ink-3">
        {r.source} · ••{r.card_last4}{r.occurred_at ? ` · ${r.occurred_at.slice(11, 16)}` : ''}
        {r.bank_ref ? ` · ref ${r.bank_ref}` : ' · no ref'}
      </div>
    </div>
  )
}

function DupRow({ c, onDone }: { c: DupCandidate; onDone: () => void }) {
  const mayEdit = useCan('ledger.housekeeping', 'edit')
  const merge = useMutation({
    mutationFn: () => mergeDuplicate(c.primary.id, c.duplicate.id),
    onSuccess: onDone,
  })
  const dismiss = useMutation({
    mutationFn: () => dismissDuplicate(c.primary.fingerprint, c.duplicate.fingerprint),
    onSuccess: onDone,
  })
  const busy = merge.isPending || dismiss.isPending
  return (
    <div className="rounded-2xl border border-line bg-surface p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-ink-3">{c.primary.date}</span>
        <span className="money text-xs font-bold text-ink">{formatINR(c.amount)}</span>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <Side r={c.primary} />
        <span className="shrink-0 text-ink-3">↔</span>
        <Side r={c.duplicate} />
      </div>
      {mayEdit && (
      <div className="mt-2 flex items-center gap-2">
        <button onClick={() => merge.mutate()} disabled={busy}
          className="flex-1 rounded-input bg-accent px-3 py-1.5 text-xs font-semibold text-surface disabled:opacity-50">
          {merge.isPending ? 'Merging…' : 'Same payment — merge'}
        </button>
        <button onClick={() => dismiss.mutate()} disabled={busy}
          className="flex-1 rounded-input border border-line px-3 py-1.5 text-xs font-semibold text-ink-2 disabled:opacity-50">
          {dismiss.isPending ? '…' : 'Keep both'}
        </button>
      </div>
      )}
    </div>
  )
}
