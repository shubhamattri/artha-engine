import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  previewDuplicates,
  applyDuplicates,
  undoDuplicates,
  formatINR,
  type DupGroup,
} from '../api/client'
import { Icon } from './ui'
import { ViewOnly, useCan } from '../auth/Can'

const SRC_LABEL: Record<string, string> = {
  statement: 'Statement', sms: 'SMS', gmail: 'Email alert', manual: 'Manual',
}

/** Reconciliation inbox: the same real transaction captured by SMS + email + statement (or a
 *  re-uploaded statement) gets collapsed to ONE. High-confidence merges in a tap; anything
 *  unsure is listed for you to decide. Fully reversible — merges only hide duplicates, never delete. */
export default function DuplicatesReview() {
  const qc = useQueryClient()
  const mayEdit = useCan('ledger.housekeeping', 'edit')
  const dup = useQuery({ queryKey: ['duplicates'], queryFn: previewDuplicates })
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)

  const refresh = () =>
    ['duplicates', 'txns', 'txn-facets', 'budget', 'cockpit', 'cashflow', 'analytics']
      .forEach((k) => qc.invalidateQueries({ queryKey: [k] }))

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn(); refresh() } finally { setBusy(false) }
  }

  const d = dup.data
  if (dup.isLoading || !d || d.group_count === 0) {
    // nothing to show unless there are duplicates (keeps the screen clean)
    return null
  }

  return (
    <div className="mb-5 rounded-2xl border border-warn/40 bg-warn/5 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 text-warn"><Icon name="alert" size={18} /></span>
          <div>
            <div className="text-sm font-semibold text-ink">
              {d.duplicate_rows} duplicate {d.duplicate_rows === 1 ? 'entry' : 'entries'} found
            </div>
            <div className="text-xs text-ink-2">
              Same transaction captured more than once (SMS + email + statement, or a re-upload).
            </div>
          </div>
        </div>
        {mayEdit && d.auto_mergeable_rows > 0 && (
          <button onClick={() => run(() => applyDuplicates(false))} disabled={busy}
            className="shrink-0 rounded-xl bg-accent px-3 py-2 text-xs font-semibold text-white hover:bg-accent-2 disabled:opacity-50">
            {busy ? 'Merging…' : `Merge ${d.auto_mergeable_rows} safe`}
          </button>
        )}
      </div>

      <button onClick={() => setOpen((v) => !v)} className="mt-3 text-xs font-semibold text-accent">
        {open ? 'Hide details' : 'Review each'}
      </button>

      {open && (
        <div className="mt-3 space-y-2.5">
          {d.groups.map((g: DupGroup) => (
            <div key={g.primary.id} className="rounded-xl border border-line bg-surface p-3">
              <div className="flex items-center justify-between">
                <span className="money text-sm font-semibold text-ink">{formatINR(g.primary.amount)}</span>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  g.confidence === 'high' ? 'bg-accent-soft text-accent' : 'bg-warn/15 text-warn'}`}>
                  {g.confidence === 'high' ? 'safe' : 'check this'}
                </span>
              </div>
              <div className="mt-1.5 space-y-1">
                <Row tag="keep" r={g.primary} />
                {g.duplicates.map((r) => <Row key={r.id} tag="drop" r={r} />)}
              </div>
            </div>
          ))}
          {/* The comparison itself is worth reading whoever you are — what goes is the merging. */}
          {!mayEdit ? <ViewOnly what="duplicate matches" /> : (
          <div className="flex flex-wrap gap-4 pt-1">
            {d.group_count > d.high_confidence && (
              <button onClick={() => run(() => applyDuplicates(true))} disabled={busy}
                className="text-xs font-semibold text-accent disabled:opacity-50">
                Merge everything shown (incl. unsure)
              </button>
            )}
            <button onClick={() => run(() => undoDuplicates())} disabled={busy}
              className="text-xs font-semibold text-ink-3 hover:text-ink">
              Undo all merges
            </button>
          </div>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ tag, r }: { tag: 'keep' | 'drop'; r: { date: string; description: string; source: string } }) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className={`w-9 shrink-0 font-semibold ${tag === 'keep' ? 'text-positive' : 'text-ink-3 line-through'}`}>
        {tag === 'keep' ? 'keep' : 'drop'}
      </span>
      <span className="font-mono text-ink-3">{r.date}</span>
      <span className="rounded bg-surface-2 px-1.5 py-0.5 text-ink-3">{SRC_LABEL[r.source] ?? r.source}</span>
      <span className="truncate text-ink-2" title={r.description}>{r.description}</span>
    </div>
  )
}
