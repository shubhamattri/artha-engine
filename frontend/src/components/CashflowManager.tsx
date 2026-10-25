import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchCashflowSummary,
  createCashflowLine,
  updateCashflowLine,
  deleteCashflowLine,
  formatINR,
  currencySymbol,
  type CashflowLine,
} from '../api/client'
import {
  Loading,
  FormRow,
  SubmitButton,
  TextInput,
  SelectInput,
  HolderOptions,
  AddPanel, ErrorState,
} from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

const VIEW_ONLY_NOUN: Record<'income' | 'fixed' | 'saving', string> = {
  income: 'what comes in each month',
  fixed: 'these fixed monthly costs',
  saving: 'these monthly savings',
}

/** Add / edit / delete recurring cash-flow lines of one kind. */
export default function CashflowManager({
  kind,
  addLabel = 'Add',
}: {
  kind: 'income' | 'fixed' | 'saving'
  addLabel?: string
}) {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['cashflow'], queryFn: fetchCashflowSummary })
  const refresh = useHouseholdRefresh()

  const [editId, setEditId] = useState<number | null>(null)
  const [draft, setDraft] = useState<{ label: string; amount_inr: string; holder: string; start_date: string }>({
    label: '', amount_inr: '', holder: 'Household', start_date: '',
  })
  const reset = { label: '', amount_inr: '', holder: 'Household', start_date: '' }

  const create = useMutation({
    mutationFn: () => createCashflowLine({
      kind, label: draft.label, amount_inr: parseFloat(draft.amount_inr) || 0,
      holder: draft.holder, active: true, start_date: draft.start_date || null,
    }),
    onSuccess: () => { setDraft(reset); refresh() },
  })
  const save = useMutation({
    mutationFn: (l: CashflowLine) => updateCashflowLine(l.id, {
      kind, label: draft.label, amount_inr: parseFloat(draft.amount_inr) || 0,
      holder: draft.holder, active: true, start_date: draft.start_date || null, end_date: l.end_date ?? null,
    }),
    onSuccess: () => { setEditId(null); refresh() },
  })
  const remove = useMutation({ mutationFn: (id: number) => deleteCashflowLine(id), onSuccess: refresh })
  const mayEdit = useCan('plan.lines', 'edit')

  if (isLoading) return <Loading rows={3} />
  if (isError || !data) return <ErrorState message="Couldn’t load your cashflow." onRetry={() => refetch()} />
  const lines = data?.lines[kind] ?? []
  const total = lines.reduce((s, l) => s + l.amount_inr, 0)

  function startEdit(l: CashflowLine) {
    setEditId(l.id)
    setDraft({ label: l.label, amount_inr: String(l.amount_inr), holder: l.holder ?? 'Household', start_date: l.start_date ?? '' })
  }

  return (
    <div className="space-y-4">
      {/* rows — one aligned column; the ₹/mo figures line up top-to-bottom */}
      <div className="space-y-3">
        {lines.map((l) => {
          if (editId === l.id)
            return (
              <FormRow
                key={l.id}
                cols={4}
                onSubmit={(e) => e.preventDefault()}
                submit={
                  <>
                    <button type="button" onClick={() => setEditId(null)} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
                    <SubmitButton type="button" disabled={save.isPending} onClick={() => save.mutate(l)}>Save</SubmitButton>
                  </>
                }
              >
                <TextInput label="Label" value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
                <SelectInput label="Holder" value={draft.holder} onChange={(e) => setDraft({ ...draft, holder: e.target.value })}>
                  <HolderOptions />
                </SelectInput>
                <TextInput label={`${currencySymbol()}/mo`} type="number" value={draft.amount_inr} onChange={(e) => setDraft({ ...draft, amount_inr: e.target.value })} />
                <TextInput label="Since (optional)" type="month" value={draft.start_date ? draft.start_date.slice(0, 7) : ''} onChange={(e) => setDraft({ ...draft, start_date: e.target.value ? `${e.target.value}-01` : '' })} />
              </FormRow>
            )
          // Clean aligned line item: name + holder on the left, the ₹/mo figure right-aligned and
          // prominent, edit/delete grouped beneath it. (A single value in RecordRow's field-band
          // floated mid-row — this is the fix.)
          return (
            <div key={l.id} className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-4">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-ink">{l.label}</div>
                {l.holder && <div className="mt-0.5 truncate text-xs text-ink-3">{l.holder}</div>}
              </div>
              <div className="shrink-0 text-right">
                <div className="money text-base font-bold tabular-nums text-ink">
                  {formatINR(l.amount_inr)}
                  <span className="ml-0.5 text-[11px] font-medium text-ink-3">/mo</span>
                </div>
                {mayEdit && (
                  <div className="mt-1 flex items-center justify-end gap-3">
                    <button onClick={() => startEdit(l)} className="inline-flex min-h-11 items-center px-2 text-[11px] font-semibold tracked text-ink-3 transition-colors hover:text-accent">Edit</button>
                    <button onClick={() => { if (confirm(`Delete "${l.label}"?`)) remove.mutate(l.id) }} className="inline-flex min-h-11 items-center px-2 text-[11px] font-semibold tracked text-negative/70 transition-colors hover:text-negative">Delete</button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {lines.length === 0 && (
          <div className="rounded-2xl border border-dashed border-line bg-surface-2 px-4 py-6 text-center text-sm text-ink-3">
            {mayEdit ? 'Nothing yet — add the first below.' : 'Nothing here yet.'}
          </div>
        )}
      </div>

      {/* total */}
      <div className="flex items-center justify-between border-t border-line px-1 pt-3">
        <span className="tracked text-xs font-semibold text-ink-3">Total / month</span>
        <span className="money text-sm font-bold text-accent">{formatINR(total)}</span>
      </div>

      {/* add line — the sentence names this kind of line, because all three kinds render this
          component side by side and "these lines" would not say which ones. */}
      {!mayEdit ? <ViewOnly what={VIEW_ONLY_NOUN[kind]} /> : (
      <AddPanel label={addLabel}>
        <FormRow
          cols={4}
          onSubmit={(e) => e.preventDefault()}
          submit={
            <SubmitButton type="button" disabled={create.isPending || !draft.label || !draft.amount_inr} onClick={() => create.mutate()}>
              {addLabel}
            </SubmitButton>
          }
        >
          <TextInput label="Label" placeholder="e.g. Rent, Groww SIP" value={editId ? '' : draft.label} onChange={(e) => { setEditId(null); setDraft({ ...draft, label: e.target.value }) }} />
          <SelectInput label="Holder" value={draft.holder} onChange={(e) => setDraft({ ...draft, holder: e.target.value })}>
            <HolderOptions />
          </SelectInput>
          <TextInput label={`${currencySymbol()}/mo`} type="number" placeholder={`${currencySymbol()} / mo`} value={editId ? '' : draft.amount_inr} onChange={(e) => setDraft({ ...draft, amount_inr: e.target.value })} />
          <TextInput label="Since (optional)" type="month" value={editId ? '' : (draft.start_date ? draft.start_date.slice(0, 7) : '')} onChange={(e) => setDraft({ ...draft, start_date: e.target.value ? `${e.target.value}-01` : '' })} />
        </FormRow>
      </AddPanel>
      )}
    </div>
  )
}
