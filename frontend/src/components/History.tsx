import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchAuditHistory, undoAuditEvent, type AuditEvent } from '../api/audit'
import { formatINR } from '../api/client'
import { useCan } from '../auth/Can'
import { EmptyState, ErrorState, Loading } from './ui'

/**
 * What changed, who changed it, and putting it back.
 *
 * The household had 992 recorded changes and no way to see one of them. Every number in this app is
 * the result of parsing, a rule, or somebody correcting something by hand, and "why is this figure
 * what it is" was answerable only by opening the database.
 *
 * Rendered per transaction inside the edit sheet, and household-wide on its own screen.
 */

/** "3 minutes ago" beats a timestamp for recent things and loses to one for old things. */
function when(iso: string | null): string {
  if (!iso) return ''
  const then = new Date(iso)
  const mins = Math.round((Date.now() - then.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)} hr ago`
  if (mins < 60 * 24 * 7) return `${Math.round(mins / 1440)} days ago`
  return then.toLocaleString('en-IN', { day: 'numeric', month: 'short' })
}

/** Amounts read as money; everything else is shown as it was stored. */
function value(field: string | null, raw: string | null): string {
  if (raw === null || raw === '' || raw === 'None') return 'nothing'
  if (field === 'amount') {
    const n = Number(raw)
    return Number.isFinite(n) ? formatINR(n) : raw
  }
  return raw
}

const FIELD_LABELS: Record<string, string> = {
  amount: 'the amount', date: 'the date', merchant: 'the merchant', category: 'the category',
  account: 'the account', direction: 'the direction', txn_type: 'the kind', archived: 'archived',
}

/** Who, in a form a person recognises. `actor` says what kind of thing acted; only one of them is
 *  a person, and before authorship was recorded even that one has no name. */
function who(e: AuditEvent): string {
  if (e.user_name) return e.user_name
  if (e.actor === 'sync') return 'The nightly sync'
  if (e.actor === 'ai') return 'Artha'
  if (e.actor === 'dedup') return 'Duplicate cleanup'
  return 'Someone'
}

/** One sentence per event. A table of action/field/old/new is a database dump wearing a UI. */
function sentence(e: AuditEvent): string {
  const field = e.field ? FIELD_LABELS[e.field] ?? `the ${e.field}` : ''
  switch (e.action) {
    case 'create': return 'added this'
    case 'archive': return 'archived this'
    case 'unarchive': return 'brought this back'
    case 'delete': return 'deleted this for good'
    case 'merge': return 'merged this as a duplicate'
    case 'undo': return `put ${field || 'this'} back to ${value(e.field, e.new_value)}`
    case 'categorize':
    case 'recategorize':
      return `filed this under ${value('category', e.new_value)}`
    default:
      return `changed ${field} from ${value(e.field, e.old_value)} to ${value(e.field, e.new_value)}`
  }
}

/** The server refuses the rest with a sentence; not offering the button is kinder than a 400. */
const UNDOABLE = new Set(['edit', 'archive', 'categorize', 'recategorize', 'undo'])

function Row({ e, showLink }: { e: AuditEvent; showLink: boolean }) {
  const qc = useQueryClient()
  const mayEdit = useCan('spending', 'edit')
  const [err, setErr] = useState<string | null>(null)

  const undo = useMutation({
    mutationFn: () => undoAuditEvent(e.id),
    onSuccess: () => { setErr(null); qc.invalidateQueries() },
    // The refusals are written to be read: "Can't undo a permanent delete (the row is gone)".
    onError: (x: unknown) => setErr(
      (x as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      ?? 'Could not undo that.'),
  })

  return (
    <div className="border-b border-line py-2 last:border-0">
      <div className="flex items-baseline gap-2">
        <p className="min-w-0 flex-1 text-[13px] leading-relaxed text-ink-2">
          <span className="font-medium text-ink">{who(e)}</span> {sentence(e)}
          {showLink && e.txn_id != null && (
            <span className="text-ink-3"> · on transaction #{e.txn_id}</span>
          )}
        </p>
        <span className="shrink-0 text-[11px] text-ink-3">{when(e.created_at)}</span>
      </div>
      {mayEdit && UNDOABLE.has(e.action) && e.txn_id != null && (
        <button onClick={() => undo.mutate()} disabled={undo.isPending}
          className="mt-1 min-h-11 text-[11.5px] font-semibold text-accent hover:underline disabled:opacity-50">
          {undo.isPending ? 'Putting it back…' : 'Undo this'}
        </button>
      )}
      {err && <p className="mt-1 text-[11.5px] text-negative">{err}</p>}
    </div>
  )
}

export default function History({ txnId, limit = 100 }: { txnId?: number; limit?: number }) {
  const q = useQuery({
    queryKey: ['audit-history', txnId ?? 'all', limit],
    queryFn: () => fetchAuditHistory(txnId, limit),
  })

  if (q.isLoading) return <Loading rows={3} />
  if (q.isError) return <ErrorState message="Couldn’t load the history." onRetry={() => q.refetch()} />
  if (!q.data?.length) {
    return (
      <EmptyState
        icon="list"
        title="Nothing has changed yet"
        hint={txnId != null
          ? 'Every edit to this transaction will be listed here, with a way to put it back.'
          : 'Every correction anyone makes is recorded here, with a way to put it back.'}
      />
    )
  }

  return <div>{q.data.map((e) => <Row key={e.id} e={e} showLink={txnId == null} />)}</div>
}
