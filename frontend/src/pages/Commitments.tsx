import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchCommitmentsReconcile,
  fetchCommitmentCandidates,
  linkCommitment,
  unlinkCommitment,
  currentMonth,
  formatINR,
  type CommitmentPaid,
  type CommitmentMissing,
  type CommitmentExtra,
} from '../api/client'
import {
  PageContainer,
  PageGrid,
  SectionHeader,
  StatGrid,
  StatCard,
  Card,
  RecordRow,
  Chip,
  FilterPanel,
  type RecordField,
} from '../components/ui'
import { fetchCommitments } from '../api/commitments'
import { useCan } from '../auth/Can'

function shiftMonth(m: string, delta: number): string {
  const [y, mo] = m.split('-').map(Number)
  const d = new Date(y, mo - 1 + delta, 1)
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
}
function monthLabel(m: string): string {
  const [y, mo] = m.split('-').map(Number)
  return new Date(y, mo - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' })
}

const KIND_LABEL: Record<string, string> = {
  EMI: 'EMI', SIP: 'SIP', Fixed: 'Fixed', Saving: 'Saving', Insurance: 'Insurance',
}
const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind

function PaidRow({ c, month }: { c: CommitmentPaid; month: string }) {
  const qc = useQueryClient()
  const mayEdit = useCan('commitments', 'edit')
  const off = Math.abs(c.actual - c.amount) >= 1
  const unlink = useMutation({
    mutationFn: () => unlinkCommitment(c.signature ?? ''),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['commitments-reconcile', month] }),
  })
  const fields: RecordField[] = [{ label: 'Paid', value: formatINR(c.actual), mono: true }]
  if (off) fields.push({ label: 'Plan', value: formatINR(c.amount), mono: true, tone: 'default' })
  // Only identity (learned/confirmed) matches are undoable — an amount match isn't a saved link.
  const undoable = c.via === 'identity' && !!c.signature
  return (
    <div>
      <RecordRow
        title={c.name}
        subtitle={`${kindLabel(c.kind)} · ${c.payee} · ${c.txn_date}`}
        fields={fields}
      />
      {undoable && mayEdit && (
        <button
          onClick={() => { if (confirm(`Unlink "${c.payee}" from ${c.name}? It'll show as missing again.`)) unlink.mutate() }}
          className="mt-1 inline-flex min-h-11 items-center px-2 text-[11px] font-medium text-ink-3 hover:text-negative"
        >
          Not {c.payee}? Unlink
        </button>
      )}
    </div>
  )
}

// The exception queue: a missing commitment you can resolve by pointing at the debit that actually paid
// it. For a household member's pocket money or a parent's support (person-transfers the system won't
// guess by amount), you confirm the payee once here and every future payment to them auto-reconciles.
function MissingRow({ c, month }: { c: CommitmentMissing; month: string }) {
  const qc = useQueryClient()
  const mayEdit = useCan('commitments', 'edit')
  const [open, setOpen] = useState(false)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['commitment-candidates', c.ref, month],
    queryFn: () => fetchCommitmentCandidates(c.ref, month),
    enabled: open,
  })
  const link = useMutation({
    mutationFn: (txn_id: number) => linkCommitment(txn_id, c.ref),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['commitments-reconcile', month] })
      qc.invalidateQueries({ queryKey: ['commitment-candidates', c.ref, month] })
      setOpen(false)
    },
  })
  const cands = data?.candidates ?? []
  return (
    <div>
      <RecordRow
        title={c.name}
        subtitle={kindLabel(c.kind)}
        fields={[{ label: 'Missing', value: formatINR(c.amount), mono: true, tone: 'negative' }]}
      />
      {/* The candidate list only opens from here, so hiding this hides the linking UI with it. */}
      {mayEdit && (
        <button
          onClick={() => setOpen((o) => !o)}
          className="mt-1 inline-flex min-h-11 items-center px-2 text-[11px] font-semibold tracked text-ink-3 transition-colors hover:text-accent"
        >
          {open ? 'Close' : 'Link a payment ▸'}
        </button>
      )}
      {open && (
        <div className="mt-2 space-y-1.5 rounded-xl border border-line bg-surface-2 p-2.5">
          {isLoading && <p className="text-xs text-ink-3">Finding this month’s payments…</p>}
          {/* The empty message below asserts a fact about your money: that no debit exists to link.
              A failed request must never be allowed to say that — it looks like an answer, and you
              would conclude the payment came from an untracked account and stop looking. */}
          {isError && (
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-negative">Couldn’t check this month’s payments.</p>
              <button onClick={() => refetch()}
                className="inline-flex min-h-11 items-center px-2 text-xs font-semibold text-accent">
                Try again
              </button>
            </div>
          )}
          {!isLoading && !isError && cands.length === 0 && (
            <p className="text-xs text-ink-3">No unmatched {c.kind === 'EMI' ? 'EMI' : 'transfer'} debit this month to link. It may have been paid from an untracked account.</p>
          )}
          {cands.map((t) => (
            <div key={t.txn_id} className="flex items-center justify-between gap-2 rounded-lg bg-surface px-2.5 py-1.5">
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-ink">{t.payee}</div>
                <div className="truncate text-[11px] text-ink-3">{t.category} · {t.date}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span className="money text-xs font-bold tabular-nums text-ink">{formatINR(t.amount)}</span>
                <button
                  onClick={() => link.mutate(t.txn_id)}
                  disabled={link.isPending}
                  className="inline-flex min-h-11 items-center rounded-input bg-accent px-3 text-[11px] font-semibold text-surface disabled:opacity-50"
                >
                  This one
                </button>
              </div>
            </div>
          ))}
          <p className="pt-0.5 text-[10px] leading-snug text-ink-3">Confirm the payee once — every future payment to them reconciles automatically and leaves your variable spend.</p>
        </div>
      )}
    </div>
  )
}

function ExtraRow({ c }: { c: CommitmentExtra }) {
  return (
    <RecordRow
      title={c.payee}
      subtitle={`${c.category} · ${c.date}`}
      fields={[{ label: 'Debited', value: formatINR(c.amount), mono: true }]}
    />
  )
}

function Section({ title, count, hint, children }: {
  title: string; count: number; hint?: string; children: React.ReactNode
}) {
  if (count === 0) return null
  return (
    <Card>
      <SectionHeader level={2} title={`${title} · ${count}`} sub={hint} />
      <PageGrid cols={2} gap="gap-3" className="mt-4">
        {children}
      </PageGrid>
    </Card>
  )
}

/**
 * The standing list, before any of it is matched against what actually went out.
 *
 * Every module owns its own commitments and none of them could see the others, so the total a
 * household owes each month existed nowhere. The reconciliation below answers "did it get paid";
 * this answers "what is it".
 */
function Standing({ month }: { month: string }) {
  const q = useQuery({
    queryKey: ['commitments-list', month],
    queryFn: () => fetchCommitments(month),
  })
  const [open, setOpen] = useState(false)

  if (!q.data?.commitments.length) return null
  const byKind = q.data.commitments.reduce<Record<string, { n: number; total: number }>>((acc, c) => {
    const k = acc[c.kind] ?? { n: 0, total: 0 }
    acc[c.kind] = { n: k.n + 1, total: k.total + c.amount }
    return acc
  }, {})

  return (
    <Card>
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-ink">Committed every month</div>
          <div className="mt-0.5 text-[11.5px] text-ink-3">
            {Object.entries(byKind)
              .sort((a, b) => b[1].total - a[1].total)
              .map(([kind, v]) => `${v.n} ${kind}`)
              .join(' · ')}
          </div>
        </div>
        <span className="money shrink-0 text-lg font-medium text-ink">
          {formatINR(q.data.monthly_total)}
        </span>
      </div>

      <button onClick={() => setOpen((o) => !o)}
        className="mt-2 min-h-11 text-[11.5px] font-semibold text-ink-2 hover:text-ink">
        {open ? 'Hide the list' : 'See all of them ▸'}
      </button>

      {open && (
        <div className="mt-1">
          {q.data.commitments.sort((a, b) => b.amount - a.amount).map((c) => (
            <div key={c.ref} className="flex items-baseline gap-2 border-b border-line py-1.5 text-[13px] last:border-0">
              <span className="min-w-0 flex-1 truncate text-ink-2">{c.name}</span>
              <Chip>{c.kind}</Chip>
              <span className="money shrink-0 text-ink">{formatINR(c.amount)}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

export default function Commitments() {
  // The hint below names a button; without the permission that button is not there, and a hint
  // pointing at a control nobody can see reads as the app having lost it.
  const mayLink = useCan('commitments', 'edit')
  const [month, setMonth] = useState(currentMonth())
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['commitments-reconcile', month],
    queryFn: () => fetchCommitmentsReconcile(month),
  })

  // month navigator: quick-step arrows + the shared FilterPanel (single/required mode) as the picker,
  // so jumping to any month uses the same panel UX as every filter in the app.
  const monthNav = (
    <div className="flex items-center gap-1 text-ink">
      <button onClick={() => setMonth((m) => shiftMonth(m, -1))} className="inline-flex min-h-11 min-w-11 items-center justify-center text-lg text-ink-2 hover:text-ink" aria-label="Previous month">‹</button>
      <FilterPanel
        triggerLabel={monthLabel(month)}
        icon="calendar"
        filters={[{ key: 'month', label: 'Month', type: 'month', value: month, onChange: (v) => v && setMonth(v) }]}
      />
      <button onClick={() => setMonth((m) => shiftMonth(m, 1))} className="inline-flex min-h-11 min-w-11 items-center justify-center text-lg text-ink-2 hover:text-ink" aria-label="Next month">›</button>
    </div>
  )

  return (
    <PageContainer>
      <SectionHeader
        title="Commitments"
        sub="Your defined EMIs, SIPs, rent & fixed expenses vs the debits that actually paid them."
        action={monthNav}
      />

      {isLoading && <p className="text-sm text-ink-3">Reconciling…</p>}
      {isError && (
        <p className="text-sm text-negative">
          Couldn’t load. <button onClick={() => refetch()} className="underline">Retry</button>
        </p>
      )}

      <Standing month={month} />

      {data && (
        <>
          <StatGrid min="11rem">
            <StatCard label="Expected" value={formatINR(data.expected_total)}
              sub="every EMI, SIP, rent & fixed bill due this month" />
            <StatCard label="Paid" value={formatINR(data.paid_total)} tone="positive"
              sub="matched to a real debit that went out" />
            <StatCard
              label="Missing"
              value={formatINR(data.missing_total)}
              tone={data.missing_total > 0 ? 'negative' : 'default'}
              sub="defined, but no matching debit found yet"
            />
          </StatGrid>
          <p className="-mt-1 text-xs text-ink-3">Expected = your planned committed outflow this month. Paid = what <span className="font-medium">actually</span> went out (real debits, so a first/broken-period EMI shows true); Missing = still due.</p>

          {/* Missing and Not-tracked FIRST, per the handoff, and Paid last. The page led with Paid,
              which is the one section that asks nothing of you: eleven rows of "this is fine" before
              the twelve that need a decision and the seven that need identifying. Reading order is
              priority whether or not it was chosen as one. */}
          <Section title="Missing this month" count={data.missing.length}
            hint={mayLink
              ? 'Defined commitments with no matching debit. If it paid from a tracked account, hit “Link a payment” to point at the real debit — confirm the payee once and it reconciles for good.'
              : 'Defined commitments with no matching debit this month.'}>
            {data.missing.sort((a, b) => b.amount - a.amount).map((c) => <MissingRow key={c.ref} c={c} month={month} />)}
          </Section>

          <Section title="Not tracked" count={data.extra.length}
            hint="Recurring-looking debits that match no defined commitment — could be a one-off (part-payment, first EMI) or something to add to Debt/Wealth.">
            {data.extra.sort((a, b) => b.amount - a.amount).map((c) => <ExtraRow key={c.txn_id} c={c} />)}
          </Section>

          <Section title="Paid" count={data.paid.length}>
            {data.paid.sort((a, b) => b.amount - a.amount).map((c) => <PaidRow key={c.ref} c={c} month={month} />)}
          </Section>

          {data.paid.length === 0 && data.missing.length === 0 && data.extra.length === 0 && (
            <p className="py-10 text-center text-sm text-ink-3">No commitments defined yet.</p>
          )}
        </>
      )}
    </PageContainer>
  )
}
