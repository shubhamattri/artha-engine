import { Fragment, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchDisposals,
  updateDisposal,
  deleteDisposal,
  recordReceipt,
  undoReceipt,
  formatINR,
  currencySymbol,
  type Disposal,
  type DisposalInput,
  type ReceiptInput,
} from '../api/client'
import {
  Loading,
  ErrorState,
  EmptyState,
  StatCard,
  StatGrid,
  RecordRow,
  DetailCell,
  FormRow,
  SubmitButton,
  TextInput,
  type RecordField,
} from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

type Draft = {
  disposed_on: string
  acquired_on: string
  quantity: string
  gross_proceeds_inr: string
  costs_inr: string
  loan_settled_inr: string
  receivable_inr: string
  cost_basis_inr: string
  notes: string
}

const BLANK: Draft = {
  disposed_on: new Date().toISOString().slice(0, 10),
  acquired_on: '', quantity: '', gross_proceeds_inr: '', costs_inr: '',
  loan_settled_inr: '', receivable_inr: '', cost_basis_inr: '', notes: '',
}

const s = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v))

export function draftFrom(d: Disposal): Draft {
  return {
    disposed_on: d.disposed_on,
    acquired_on: d.acquired_on ?? '',
    quantity: s(d.quantity),
    gross_proceeds_inr: s(d.gross_proceeds_inr),
    costs_inr: s(d.costs_inr),
    loan_settled_inr: s(d.loan_settled_inr),
    receivable_inr: s(d.receivable_inr),
    cost_basis_inr: s(d.cost_basis_inr),
    notes: d.notes ?? '',
  }
}

/** Blank stays blank: an empty cost basis means UNKNOWN, and must never become a zero. */
const num = (v: string): number | null => (v.trim() === '' ? null : Number(v))
const zero = (v: string): number => num(v) ?? 0

function payload(d: Draft): DisposalInput {
  return {
    disposed_on: d.disposed_on,
    gross_proceeds_inr: Number(d.gross_proceeds_inr),
    acquired_on: d.acquired_on || null,
    quantity: num(d.quantity),
    costs_inr: zero(d.costs_inr),
    loan_settled_inr: zero(d.loan_settled_inr),
    receivable_inr: zero(d.receivable_inr),
    cost_basis_inr: num(d.cost_basis_inr),
    notes: d.notes || null,
  }
}

const detailOf = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail

/** The gain the tax is computed on. Costs come off it; the loan settled deliberately does not. */
const taxableGain = (d: Draft): number | null => {
  const basis = num(d.cost_basis_inr)
  if (basis === null) return null
  return Number(d.gross_proceeds_inr || 0) - zero(d.costs_inr) - basis
}

const reachesBank = (d: Draft): number =>
  Number(d.gross_proceeds_inr || 0) - zero(d.costs_inr) - zero(d.loan_settled_inr) - zero(d.receivable_inr)

/**
 * SaleForm — used both to record a sale and to correct one already recorded (two sales are in the
 * ledger with placeholder dates that only this form can fix).
 *
 * The copy carries the one distinction that costs real money if it is missed: selling COSTS reduce
 * the taxable gain, a LOAN SETTLED out of the proceeds reduces only what reaches the bank.
 */
export function SaleForm({
  initial, holding, label, busy, error, mode = 'record', onCancel, onSubmit,
}: {
  initial?: Draft
  /** how many units the asset holds — shows the part-sale field and bounds it */
  holding?: number | null
  label: string
  busy?: boolean
  error?: unknown
  /**
   * 'record' = entering a sale, where the receivable is simply part of the deal.
   * 'correct' = fixing a figure that was typed wrong. The field stays editable because two sales in
   * the ledger carry placeholder numbers and a mistyped amount has to be fixable — but on this path
   * it is the wrong tool for money that has actually arrived, and the hint says so.
   */
  mode?: 'record' | 'correct'
  onCancel?: () => void
  onSubmit: (body: DisposalInput) => void
}) {
  const [d, setD] = useState<Draft>(initial ?? BLANK)
  const mayEdit = useCan('wealth', 'edit')
  const cur = currencySymbol()   // the household's currency, never a hardcoded ₹
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD({ ...d, [k]: e.target.value })
  const gain = taxableGain(d)
  const ready = d.disposed_on !== '' && Number(d.gross_proceeds_inr) > 0

  if (!mayEdit) return <ViewOnly what="this sale" />

  return (
    <FormRow
      cols={3}
      onSubmit={(e) => e.preventDefault()}
      submit={
        <>
          {onCancel && (
            <button type="button" onClick={onCancel} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
          )}
          <SubmitButton type="button" disabled={busy || !ready} onClick={() => onSubmit(payload(d))}>{label}</SubmitButton>
        </>
      }
    >
      <TextInput label="Sale date" type="date" value={d.disposed_on} onChange={set('disposed_on')} />
      <TextInput
        label={`Gross proceeds ${cur}`} type="number" value={d.gross_proceeds_inr} onChange={set('gross_proceeds_inr')}
        hint="What the buyer paid. This is the sale consideration for capital gains — not what landed in your bank."
      />
      <TextInput
        label="Acquired on" type="date" value={d.acquired_on} onChange={set('acquired_on')}
        hint="Sets long-term vs short-term, which sets the rate. Leave blank if genuinely unknown."
      />
      <TextInput
        label={`Selling costs ${cur}`} type="number" value={d.costs_inr} onChange={set('costs_inr')}
        hint="Brokerage, stamp duty, legal. These REDUCE the taxable gain."
      />
      <TextInput
        label={`Loan settled from proceeds ${cur}`} type="number" value={d.loan_settled_inr} onChange={set('loan_settled_inr')}
        hint="Proceeds that went straight to closing a loan on this asset. Reduces what reaches your bank — reduces the taxable gain by NOTHING."
      />
      <TextInput
        label={`Still to be received ${cur}`} type="number" value={d.receivable_inr} onChange={set('receivable_inr')}
        hint={mode === 'correct'
          ? 'Only for fixing a wrong figure. When money actually arrives use “Money received” — lowering this on its own takes it off your net worth instead of moving it into cash.'
          : 'Agreed but not in your account yet. Stays on your net worth as a receivable until you record it arriving.'}
      />
      <TextInput
        label={`Cost basis ${cur}`} type="number" value={d.cost_basis_inr} onChange={set('cost_basis_inr')}
        hint="What it originally cost. Leave blank if unknown — the gain then reads “not computable”, never zero. For ESOP shares the basis is the FMV on each exercise date (already taxed as a perquisite), NOT the discounted price you paid."
      />
      {(holding !== null && holding !== undefined) || d.quantity !== '' ? (
        <TextInput
          label="Quantity sold" type="number" value={d.quantity} onChange={set('quantity')}
          hint={holding !== null && holding !== undefined
            ? `Leave blank to sell the whole holding of ${holding}.`
            : 'Leave blank if the whole asset went.'}
        />
      ) : null}
      <TextInput label="Note" value={d.notes} onChange={set('notes')} placeholder="e.g. registration pending" />

      <div className="col-span-full grid grid-cols-1 gap-4 rounded-2xl border border-line bg-surface p-4 sm:grid-cols-2">
        <DetailCell
          label="Reaches your bank" value={formatINR(reachesBank(d))}
          hint="gross − selling costs − loan settled − still receivable. Not a tax number."
        />
        <DetailCell
          label="Taxable gain" accent
          value={gain === null ? 'Not computable' : formatINR(gain)}
          hint={gain === null
            ? 'The cost basis is blank, so the gain is unknown — it is not zero and no tax can be estimated.'
            : 'gross − selling costs − cost basis. The loan settled is deliberately not in this line.'}
        />
      </div>
      {error != null && (
        <div className="col-span-full text-xs text-negative">{detailOf(error) ?? 'Could not save that.'}</div>
      )}
    </FormRow>
  )
}

function DisposalDetail({ d, onDone }: { d: Disposal; onDone: () => void }) {
  const save = useMutation({
    mutationFn: (body: DisposalInput) => updateDisposal(d.id as number, body),
    onSuccess: onDone,
  })
  return <SaleForm initial={draftFrom(d)} label="Save correction" mode="correct" busy={save.isPending}
                   error={save.error} onCancel={onDone} onSubmit={(b) => save.mutate(b)} />
}

/**
 * ReceiptForm — recording money landing against a sale receivable.
 *
 * This exists because the alternative was editing "Still to be received" downwards, which lowers
 * one number and raises nothing: the money leaves net worth instead of moving into cash. One submit
 * here is one API call that moves both sides or neither.
 */
function ReceiptForm({ d, onDone }: { d: Disposal; onDone: () => void }) {
  const mayEdit = useCan('wealth', 'edit')
  const cur = currencySymbol()
  const outstanding = d.receivable_inr ?? 0
  const [amount, setAmount] = useState(String(outstanding))
  const [on, setOn] = useState(new Date().toISOString().slice(0, 10))
  const [note, setNote] = useState('')
  const save = useMutation({
    mutationFn: (body: ReceiptInput) => recordReceipt(d.id as number, body),
    onSuccess: onDone,
  })
  const value = Number(amount)
  const ready = on !== '' && value > 0 && value <= outstanding
  const left = outstanding - (value > 0 ? value : 0)

  if (!mayEdit) return <ViewOnly what="money received against this sale" />

  return (
    <FormRow
      cols={3}
      onSubmit={(e) => e.preventDefault()}
      submit={
        <>
          <button type="button" onClick={onDone} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
          <SubmitButton type="button" disabled={save.isPending || !ready}
                        onClick={() => save.mutate({ received_on: on, amount_inr: value, notes: note || null })}>
            Record it
          </SubmitButton>
        </>
      }
    >
      <TextInput
        label={`Amount received ${cur}`} type="number" value={amount} onChange={(e) => setAmount(e.target.value)}
        hint={`${formatINR(outstanding)} is still outstanding. More than that is refused — if the agreed amount was wrong, correct the sale instead.`}
        error={value > outstanding ? `That is more than the ${formatINR(outstanding)} still owed.` : undefined}
      />
      <TextInput label="Date it landed" type="date" value={on} onChange={(e) => setOn(e.target.value)}
                 hint="When it reached your account, not when it was promised." />
      <TextInput label="Note" value={note} onChange={(e) => setNote(e.target.value)}
                 placeholder="e.g. registration completed" />
      <div className="col-span-full grid grid-cols-1 gap-4 rounded-2xl border border-line bg-surface p-4 sm:grid-cols-2">
        <DetailCell label="Still owed after this" value={formatINR(left)}
                    hint={left === 0 ? 'This settles the sale in full.' : 'Stays on your net worth as a receivable.'} />
        <DetailCell
          label="Your net worth" accent value="Unchanged"
          hint={`The money moves from “still owed” into “${d.asset} — sale proceeds”. Nothing is added, nothing is lost.`}
        />
      </div>
      {save.error != null && (
        <div className="col-span-full text-xs text-negative">{detailOf(save.error) ?? 'Could not record that.'}</div>
      )}
    </FormRow>
  )
}

export default function DisposalsManager() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['disposals'],
    queryFn: fetchDisposals,
  })
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('wealth', 'edit')
  const [editId, setEditId] = useState<number | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)
  const [receiveId, setReceiveId] = useState<number | null>(null)
  const remove = useMutation({ mutationFn: (id: number) => deleteDisposal(id), onSuccess: refresh })
  const undo = useMutation({
    mutationFn: (v: { disposalId: number; receiptId: number }) => undoReceipt(v.disposalId, v.receiptId),
    onSuccess: refresh,
  })

  if (isLoading) return <Loading rows={2} />
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />
  const rows = data.disposals ?? []
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="list"
        title="No sales recorded"
        hint={mayEdit
          ? 'Sold something? Use “Record sale” on the asset — it stops counting as an asset and whatever is still owed to you stays on your net worth.'
          : 'Sales appear here once one is recorded. What is still owed on a sale stays on the household’s net worth.'}
      />
    )
  }

  const receivable = rows.reduce((t, r) => t + (r.receivable_inr ?? 0), 0)
  const received = rows.reduce((t, r) => t + (r.received_inr ?? 0), 0)

  return (
    <div className="space-y-5">
      <StatGrid min="12rem">
        <StatCard label="Estimated tax on realised gains" value={data.estimated_tax_inr} tone="warn"
                  sub={data.unpriced_count > 0
                    ? `${data.unpriced_count} sale${data.unpriced_count > 1 ? 's' : ''} have no cost basis — their gain is unknown, so this is an understatement`
                    : 'Planning estimate, not a tax return'} />
        <StatCard label="Still to be received" value={receivable} tone="accent"
                  sub="Counted in net worth until it lands" />
        <StatCard label="Received so far" value={received} tone="positive"
                  sub="Moved into cash against these sales — net worth unchanged by the move" />
      </StatGrid>

      <div className="space-y-3">
        {rows.map((r, i) => {
          const key = r.id ?? -i - 1
          const fields: RecordField[] = [
            { label: 'Gross proceeds', value: formatINR(r.gross_proceeds_inr), mono: true },
            { label: 'Reached bank', value: formatINR(r.net_received_inr), mono: true },
            { label: 'Still receivable', value: formatINR(r.receivable_inr), mono: true,
              tone: r.receivable_inr > 0 ? 'accent' : 'default' },
            { label: 'Received', value: formatINR(r.received_inr ?? 0), mono: true,
              tone: (r.received_inr ?? 0) > 0 ? 'positive' : 'default' },
            { label: 'Realised gain', mono: true,
              tone: r.gain_inr === null ? 'default' : r.gain_inr >= 0 ? 'positive' : 'negative',
              value: r.gain_inr === null ? 'Not computable' : formatINR(r.gain_inr) },
            { label: 'Est. tax', mono: true,
              value: r.tax_inr === null ? '—' : formatINR(r.tax_inr) },
          ]
          return (
            <Fragment key={key}>
              <RecordRow
                title={r.asset}
                subtitle={`sold ${r.disposed_on}${r.months_held !== null ? ` · held ${r.months_held} mo` : ''}${
                  r.is_long_term === null ? '' : r.is_long_term ? ' · long-term' : ' · short-term'}`}
                fields={fields}
                actions={
                  <>
                    {mayEdit && r.id !== null && r.receivable_inr > 0 && (
                      <button onClick={() => { setReceiveId(receiveId === r.id ? null : r.id); setEditId(null) }} className="min-h-11 rounded-input bg-accent-soft px-3 text-xs font-semibold text-accent transition-colors hover:bg-accent hover:text-white">Money received</button>
                    )}
                    <button onClick={() => setOpenId(openId === key ? null : key)} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Why</button>
                    {mayEdit && r.id !== null && (
                      <>
                        <button onClick={() => { setEditId(editId === r.id ? null : r.id); setReceiveId(null) }} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Correct</button>
                        <button onClick={() => { if (confirm(`Delete the recorded sale of ${r.asset}? The asset comes back.`)) remove.mutate(r.id as number) }} className="min-h-11 px-2 text-xs font-semibold tracked text-negative/70 transition-colors hover:text-negative">Delete</button>
                      </>
                    )}
                  </>
                }
              />
              {openId === key && (
                <div className="grid grid-cols-2 gap-4 rounded-2xl border border-line bg-surface p-4 sm:grid-cols-4">
                  <DetailCell label="Selling costs" value={formatINR(r.costs_inr ?? 0)}
                              hint="Deducted from the gain" />
                  <DetailCell label="Loan settled" value={formatINR(r.loan_settled_inr)}
                              hint="Not deducted from the gain" />
                  <DetailCell label="Cost basis"
                              value={r.cost_basis_inr === null || r.cost_basis_inr === undefined ? 'Unknown' : formatINR(r.cost_basis_inr)} />
                  <DetailCell label="Acquired on" value={r.acquired_on ?? 'Unknown'} />
                  <div className="col-span-2 text-xs leading-relaxed text-ink-2 sm:col-span-4">{r.why}</div>
                  {r.notes && <div className="col-span-2 text-xs text-ink-3 sm:col-span-4">{r.notes}</div>}
                  <div className="col-span-2 sm:col-span-4">
                    <div className="text-[11px] font-medium text-ink-3">Money received against this sale</div>
                    {(r.receipts ?? []).length === 0 ? (
                      <div className="mt-1 text-xs text-ink-3">Nothing recorded yet.</div>
                    ) : (
                      <div className="mt-1 space-y-1">
                        {(r.receipts ?? []).map((p) => (
                          <div key={p.id} className="flex items-center gap-2 text-xs">
                            <span className="money min-w-[6rem] text-ink">{formatINR(p.amount_inr)}</span>
                            <span className="text-ink-3">{p.received_on}</span>
                            {p.notes && <span className="min-w-0 truncate text-ink-3">· {p.notes}</span>}
                            {mayEdit && (
                              <button
                                onClick={() => { if (confirm(`Undo ${formatINR(p.amount_inr)} received on ${p.received_on}? It goes back to still-owed and comes off the cash row.`)) undo.mutate({ disposalId: r.id as number, receiptId: p.id }) }}
                                className="ml-auto min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-negative">Undo</button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
              {receiveId !== null && receiveId === r.id && (
                <ReceiptForm d={r} onDone={() => { setReceiveId(null); refresh() }} />
              )}
              {editId !== null && editId === r.id && (
                <DisposalDetail d={r} onDone={() => { setEditId(null); refresh() }} />
              )}
            </Fragment>
          )
        })}
      </div>

      <p className="text-xs text-ink-3">
        Selling costs (brokerage, stamp duty, legal) come off the taxable gain. Money used to close a
        loan on the asset does not — it only reduces what reaches your bank. Where the cost basis is
        unknown the gain is shown as not computable, never as zero.
        {mayEdit && ' When money finally arrives use “Money received”: it lowers what is owed and raises the sale’s cash row together, so your net worth does not move. Editing “Still to be received” by hand only lowers one side.'}
      </p>
    </div>
  )
}
