import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  categorizeAi, formatINR, normalizeBanks, normalizeMerchants, pairInternalTransfers,
  recategorizeAll, type CategorizeAiResult,
} from '../api/client'
import { Card, Icon, SectionHeader } from './ui'
import { ViewOnly, useCan } from '../auth/Can'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'

/** What a sweep would do, in one shape, so a preview renders the same way whichever sweep produced it. */
interface Diff {
  changed: number
  moves: { label: string; n: number }[]
  /** Under the list: rows deliberately left alone, or a warning that the list is not the whole story. */
  note?: string
}

interface Sweep {
  key: string
  title: string
  what: string
  run: (dryRun: boolean) => Promise<Diff>
}

const SWEEPS: Sweep[] = [
  {
    key: 'merchants',
    title: 'Make known brands consistent',
    what: 'Every transaction from a brand the app recognises moves into that brand’s own category, so the same shop stops landing in three different places. Merchants you have categorised yourself are never touched.',
    run: async (dryRun) => {
      const r = await normalizeMerchants(dryRun)
      return {
        changed: r.changed,
        moves: Object.entries(r.by_merchant)
          .map(([merchant, m]) => ({ label: `${merchant} → ${m.to}`, n: m.n }))
          .sort((a, b) => b.n - a.n),
        note: r.skipped_user > 0
          ? `${r.skipped_user} left exactly as they are, because you set those merchants by hand.`
          : undefined,
      }
    },
  },
  {
    key: 'recategorize',
    title: 'Re-apply what the app has learned',
    what: 'Runs every transaction past the brand list and the categories you and the AI have already taught it. It can only correct a category — it never falls back to guessing from words, so a good category cannot be downgraded.',
    run: async (dryRun) => {
      const r = await recategorizeAll(dryRun)
      return {
        changed: r.reclassified,
        moves: Object.entries(r.moves).map(([label, n]) => ({ label, n })),
        // The server sends only the 20 biggest moves, so the list is a sample and the count is not.
        note: `Checked all ${r.total} transactions. The list below shows the 20 biggest moves, not every one.`,
      }
    },
  },
  {
    key: 'banks',
    title: 'Tidy up bank names',
    what: 'Bank alerts arrive with the card name and last four digits stuck onto the bank, which stops the app working out which billing cycle a transaction belongs to. This trims them back to just the bank. It already runs after every sync — this is here so you can look at it and run it on demand, not because it gets neglected.',
    run: async (dryRun) => {
      const r = await normalizeBanks(dryRun)
      return {
        changed: r.changed,
        moves: Object.entries(r.moves).map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n),
      }
    },
  },
]

/**
 * Ledger-wide clean-up. Every one of these rewrites categories across the whole ledger, which is
 * why three of the four show you the diff first and only then offer to write it.
 */
export default function Maintenance() {
  const mayEdit = useCan('ledger.housekeeping', 'edit')
  const refresh = useHouseholdRefresh()

  return (
    <Card>
      <SectionHeader
        level={2}
        title="Tidy up"
        sub="Sweeps that clean up categories and bank names across every transaction you have. Each one shows what it would change before it changes anything."
      />
      {!mayEdit ? (
        <div className="mt-3"><ViewOnly what="the ledger clean-up tools" /></div>
      ) : (
        <div className="mt-3 space-y-2">
          {SWEEPS.map((s) => <SweepRow key={s.key} sweep={s} onApplied={refresh} />)}
          <TransfersRow onApplied={refresh} />
          <AiRow onApplied={refresh} />
        </div>
      )}
    </Card>
  )
}

const SHOWN = 20

function SweepRow({ sweep, onApplied }: { sweep: Sweep; onApplied: () => void }) {
  const [diff, setDiff] = useState<Diff | null>(null)
  const [applied, setApplied] = useState<number | null>(null)
  const [open, setOpen] = useState(false)

  const preview = useMutation({
    mutationFn: () => sweep.run(true),
    onSuccess: (d) => { setApplied(null); setDiff(d); setOpen(true) },
  })
  // The real run answers with its own diff, so what gets reported afterwards is what was written —
  // not the preview's count, which was measured before anything moved.
  const apply = useMutation({
    mutationFn: () => sweep.run(false),
    onSuccess: (d) => { setDiff(null); setOpen(false); setApplied(d.changed); onApplied() },
  })
  const busy = preview.isPending || apply.isPending
  const failed = preview.isError || apply.isError

  return (
    <div className="rounded-2xl border border-line bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-ink">{sweep.title}</div>
          <div className="mt-0.5 text-xs leading-relaxed text-ink-3">{sweep.what}</div>
        </div>
        <button onClick={() => preview.mutate()} disabled={busy}
          className="inline-flex min-h-11 shrink-0 items-center rounded-input border border-line px-3 text-xs font-semibold text-ink transition-colors hover:border-line-2 disabled:opacity-50">
          {preview.isPending ? 'Checking…' : diff || applied !== null ? 'Check again' : 'Preview'}
        </button>
      </div>

      {failed && (
        <div className="mt-2 flex items-center gap-2 text-xs font-medium text-negative">
          <Icon name="alert" size={14} /> That did not go through. Try again.
        </div>
      )}

      {applied !== null && (
        <div className="mt-2 flex items-center gap-2 text-xs font-medium text-positive">
          <Icon name="check" size={14} />
          {applied === 0 ? 'Nothing needed changing.' : `${applied} transactions updated.`}
        </div>
      )}

      {diff && (
        <div className="mt-2 rounded-xl border border-line bg-surface-2 p-3">
          {diff.changed === 0 ? (
            <div className="text-xs text-ink-2">Nothing would change. Everything here is already tidy.</div>
          ) : (
            <>
              <div className="text-xs font-semibold text-ink">
                {diff.changed} {diff.changed === 1 ? 'transaction' : 'transactions'} would change
              </div>
              {open && diff.moves.length > 0 && (
                <div className="mt-2 space-y-1">
                  {diff.moves.slice(0, SHOWN).map((m) => (
                    <div key={m.label} className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="truncate text-ink-2" title={m.label}>{m.label}</span>
                      <span className="money shrink-0 font-semibold text-ink-3">{m.n}</span>
                    </div>
                  ))}
                  {diff.moves.length > SHOWN && (
                    <div className="text-[11px] text-ink-3">and {diff.moves.length - SHOWN} more</div>
                  )}
                </div>
              )}
              {diff.note && <div className="mt-2 text-[11px] leading-relaxed text-ink-3">{diff.note}</div>}
              <div className="mt-3 flex flex-wrap items-center gap-4">
                <button onClick={() => apply.mutate()} disabled={busy}
                  className="inline-flex min-h-11 items-center rounded-input bg-accent px-3 text-xs font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50">
                  {apply.isPending ? 'Applying…' : `Apply these ${diff.changed}`}
                </button>
                {diff.moves.length > 0 && (
                  <button onClick={() => setOpen((v) => !v)} className="text-xs font-semibold text-accent">
                    {open ? 'Hide the list' : 'Show the list'}
                  </button>
                )}
                <button onClick={() => setDiff(null)} disabled={busy}
                  className="text-xs font-semibold text-ink-3 hover:text-ink disabled:opacity-50">
                  Leave it
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * The AI pass has no dry run — reading the transactions IS the work, so a preview would cost the
 * same as the run. It is bounded instead: only rows the app has not cleaned up, never one you confirmed.
 */
/**
 * Money moved between the household's OWN accounts is not spending, and until it is paired it is
 * counted twice — once leaving one account, once arriving in another. Sync already does this after
 * every import; this is here to run it on demand and to say what it found.
 */
function TransfersRow({ onApplied }: { onApplied: () => void }) {
  const run = useMutation({
    mutationFn: () => pairInternalTransfers(),
    onSuccess: (r) => { if (r.transactions_tagged > 0) onApplied() },
  })

  return (
    <div className="rounded-2xl border border-line bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-ink">Pair up transfers between your accounts</div>
          <div className="mt-0.5 text-xs leading-relaxed text-ink-3">
            Money moved from one of your accounts to another is not spending, but it shows up twice —
            leaving one and arriving in the other. This matches those pairs so they stop inflating
            what you appear to have spent. It runs after every sync too.
          </div>
        </div>
        <button onClick={() => run.mutate()} disabled={run.isPending}
          className="inline-flex min-h-11 shrink-0 items-center rounded-input bg-accent px-3 text-xs font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50">
          {run.isPending ? 'Matching…' : 'Run'}
        </button>
      </div>

      {run.isError && (
        <div className="mt-2 flex items-center gap-2 text-xs font-medium text-negative">
          <Icon name="alert" size={14} /> That did not go through. Try again.
        </div>
      )}
      {run.data && (
        <div className="mt-2 flex items-start gap-2 text-xs leading-relaxed text-ink-2">
          <span className={run.data.pairs > 0 ? 'text-positive' : 'text-ink-3'}>
            <Icon name={run.data.pairs > 0 ? 'check' : 'info'} size={14} />
          </span>
          <span>
            {run.data.pairs > 0
              ? `Matched ${run.data.pairs} transfer${run.data.pairs === 1 ? '' : 's'} across ${run.data.transactions_tagged} transactions, taking ${formatINR(run.data.eliminated_total)} out of what looked like spending.`
              : 'Nothing left to match — every transfer between your accounts is already paired.'}
          </span>
        </div>
      )}
    </div>
  )
}

function AiRow({ onApplied }: { onApplied: () => void }) {
  const run = useMutation({
    mutationFn: () => categorizeAi(),
    onSuccess: (r) => { if (rowsTouched(r) > 0) onApplied() },
  })

  return (
    <div className="rounded-2xl border border-line bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-ink">Name and categorise the leftovers</div>
          <div className="mt-0.5 text-xs leading-relaxed text-ink-3">
            Asks the AI to read the transactions nothing else could place, work out the merchant, and
            file them. It skips anything you have already confirmed. There is no preview for this one —
            reading them is the work — so it writes as soon as you run it.
          </div>
        </div>
        <button onClick={() => run.mutate()} disabled={run.isPending}
          className="inline-flex min-h-11 shrink-0 items-center rounded-input bg-accent px-3 text-xs font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50">
          {run.isPending ? 'Reading…' : 'Run'}
        </button>
      </div>

      {run.isError && (
        <div className="mt-2 flex items-center gap-2 text-xs font-medium text-negative">
          <Icon name="alert" size={14} /> That did not go through. Try again.
        </div>
      )}
      {run.data && (
        <div className="mt-2 flex items-start gap-2 text-xs leading-relaxed text-ink-2">
          <span className={run.data.ok ? 'text-positive' : 'text-ink-3'}>
            <Icon name={run.data.ok ? 'check' : 'info'} size={14} />
          </span>
          <span>{aiMessage(run.data)}</span>
        </div>
      )}
    </div>
  )
}

/** Success reports rows in `rows_updated`, every no-op shape reports them in `updated`. Reading
 *  either one alone silently shows 0 for half the responses. */
function rowsTouched(r: CategorizeAiResult): number {
  return r.rows_updated ?? r.updated ?? 0
}

function aiMessage(r: CategorizeAiResult): string {
  if (!r.ok) return r.reason ?? 'Nothing was categorised.'
  if (!r.distinct) return r.note ?? 'Nothing left to categorise.'
  const rows = rowsTouched(r)
  return `${rows} ${rows === 1 ? 'transaction' : 'transactions'} filed, from ${r.categorized ?? 0} of the ${r.distinct} descriptions it had not seen before.`
}
