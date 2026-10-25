import { Fragment, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchSinking,
  createSinking,
  updateSinking,
  withdrawSinking,
  deleteSinking,
  formatINR,
  currencySymbol,
  type SinkingFund,
  type SinkingInput,
} from '../api/client'
import {
  HolderOptions,
  StatCard,
  StatGrid,
  RecordRow,
  Chip,
  FormRow,
  SubmitButton,
  TextInput,
  SelectInput,
  AddPanel,
  type RecordField, ErrorState,
} from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

const EMPTY: SinkingInput = {
  name: '', monthly_contribution: 0, opening_balance: 0, as_of_date: '',
  target_annual: null, holder: 'Household', active: true,
}

// PUT replaces every column, so an edit resends the whole fund. Withdrawals are not part of that
// body and survive untouched.
const toInput = (fd: SinkingFund): SinkingInput => ({
  name: fd.name, monthly_contribution: fd.monthly_contribution, opening_balance: fd.opening_balance,
  as_of_date: fd.as_of_date, target_annual: fd.target_annual, holder: fd.holder ?? 'Household',
  active: fd.active, notes: fd.notes,
})

function EditRow({ fd, onDone }: { fd: SinkingFund; onDone: () => void }) {
  const [f, setF] = useState<SinkingInput>(() => toInput(fd))
  const save = useMutation({ mutationFn: () => updateSinking(fd.id, f), onSuccess: onDone })
  return (
    <FormRow
      cols={3}
      onSubmit={(e) => e.preventDefault()}
      submit={
        <>
          <button type="button" onClick={onDone} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
          <SubmitButton type="button" disabled={save.isPending || !f.name || !f.as_of_date} onClick={() => save.mutate()}>Save</SubmitButton>
        </>
      }
    >
      <TextInput label="Fund" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <SelectInput label="Holder" value={f.holder ?? 'Household'} onChange={(e) => setF({ ...f, holder: e.target.value })}>
        <HolderOptions />
      </SelectInput>
      <TextInput label={`${currencySymbol()}/mo`} type="number" value={f.monthly_contribution ?? 0} onChange={(e) => setF({ ...f, monthly_contribution: parseFloat(e.target.value) || 0 })} />
      <TextInput label={`Already saved ${currencySymbol()}`} type="number" value={f.opening_balance ?? 0} onChange={(e) => setF({ ...f, opening_balance: parseFloat(e.target.value) || 0 })} />
      <TextInput label="Since" type="date" value={f.as_of_date} onChange={(e) => setF({ ...f, as_of_date: e.target.value })} />
      <TextInput label={`Covers ${currencySymbol()}/yr`} type="number" value={f.target_annual ?? ''} onChange={(e) => setF({ ...f, target_annual: parseFloat(e.target.value) || null })} />
      <SelectInput label="Status" value={f.active ? 'active' : 'paused'} onChange={(e) => setF({ ...f, active: e.target.value === 'active' })}>
        <option value="active">Active</option>
        <option value="paused">Paused</option>
      </SelectInput>
      <TextInput label="Notes" value={f.notes ?? ''} onChange={(e) => setF({ ...f, notes: e.target.value || null })} />
    </FormRow>
  )
}

function WithdrawRow({ id, onDone }: { id: number; onDone: () => void }) {
  const [amount, setAmount] = useState('')
  const [dt, setDt] = useState('')
  const [note, setNote] = useState('')
  const draw = useMutation({
    mutationFn: () => withdrawSinking(id, { amount: Number(amount), date: dt, note }),
    onSuccess: () => { setAmount(''); setDt(''); setNote(''); onDone() },
  })
  return (
    <FormRow
      cols={3}
      onSubmit={(e) => e.preventDefault()}
      submit={
        <SubmitButton type="button" disabled={draw.isPending || !amount || !dt} onClick={() => draw.mutate()}>− withdraw</SubmitButton>
      }
    >
      <TextInput label={`Amount ${currencySymbol()}`} type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
      <TextInput label="Date" type="date" value={dt} onChange={(e) => setDt(e.target.value)} />
      <TextInput label="Note" placeholder="e.g. ATQ flight" value={note} onChange={(e) => setNote(e.target.value)} />
    </FormRow>
  )
}

export default function SinkingManager() {
  const refresh = useHouseholdRefresh()
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['sinking'], queryFn: fetchSinking })
  const [f, setF] = useState<SinkingInput>(EMPTY)
  const [openId, setOpenId] = useState<number | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const create = useMutation({ mutationFn: () => createSinking(f), onSuccess: () => { setF(EMPTY); refresh() } })
  const remove = useMutation({ mutationFn: (id: number) => deleteSinking(id), onSuccess: refresh })
  const mayEdit = useCan('plan.lines', 'edit')

  if (isLoading) return <div className="text-sm text-ink-3">Loading sinking funds…</div>
  // Without this an error falls through and renders a real fund list as empty.
  if (isError || !data) return <ErrorState message="Couldn’t load your sinking funds." onRetry={() => refetch()} />
  const funds = data?.funds ?? []

  return (
    <div className="space-y-5">
      <StatGrid min="12rem">
        <StatCard label="Sitting in buckets" value={data?.total_balance ?? 0} tone="accent" />
        <StatCard label="Set aside / month" value={data?.monthly_set_aside ?? 0} />
      </StatGrid>

      <div className="space-y-3">
        {funds.map((fd) => {
          const fields: RecordField[] = [
            { label: `${currencySymbol()}/mo`, value: formatINR(fd.monthly_contribution), mono: true },
            { label: 'Paid in', value: formatINR(fd.paid_in), mono: true },
            { label: 'Spent', value: fd.spent ? formatINR(fd.spent) : '—', mono: true, tone: fd.spent ? 'negative' : 'default' },
            { label: 'Balance', value: formatINR(fd.balance), mono: true, tone: 'accent' },
          ]
          return (
            <Fragment key={fd.id}>
              <RecordRow
                onClick={() => setOpenId(openId === fd.id ? null : fd.id)}
                title={
                  <span className="inline-flex items-center gap-2">
                    {fd.name}
                    {!fd.active && <Chip>PAUSED</Chip>}
                  </span>
                }
                subtitle={fd.holder ?? undefined}
                fields={fields}
                actions={mayEdit
                  ? (
                    <>
                      <button onClick={(e) => { e.stopPropagation(); setEditId(editId === fd.id ? null : fd.id) }} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Edit</button>
                      <button onClick={(e) => { e.stopPropagation(); if (confirm(`Delete ${fd.name}?`)) remove.mutate(fd.id) }} className="min-h-11 px-2 text-xs font-semibold tracked text-negative/70 transition-colors hover:text-negative">Delete</button>
                    </>
                  )
                  : null}
              />
              {editId === fd.id && <EditRow fd={fd} onDone={() => { setEditId(null); refresh() }} />}
              {openId === fd.id && (
                <div className="space-y-2 px-1">
                  <div className="text-[11px] text-ink-3">
                    Opening {formatINR(fd.opening_balance)} as of {fd.as_of_date} · {fd.months_elapsed} months accruing
                    {fd.target_annual ? ` · covers ~${formatINR(fd.target_annual)}/yr` : ''}
                    {fd.withdrawals.length > 0 && ` · ${fd.withdrawals.length} withdrawal(s)`}
                  </div>
                  {mayEdit && <WithdrawRow id={fd.id} onDone={refresh} />}
                </div>
              )}
            </Fragment>
          )
        })}
      </div>

      {/* add fund */}
      {!mayEdit ? <ViewOnly what="these sinking funds" /> : (
      <AddPanel label="Add fund">
        <FormRow
          cols={3}
          onSubmit={(e) => e.preventDefault()}
          submit={
            <SubmitButton type="button" disabled={create.isPending || !f.name || !f.as_of_date} onClick={() => create.mutate()}>Add fund</SubmitButton>
          }
        >
          <TextInput label="Fund" placeholder="e.g. Home tickets" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <SelectInput label="Holder" value={f.holder ?? 'Household'} onChange={(e) => setF({ ...f, holder: e.target.value })}>
            <HolderOptions />
          </SelectInput>
          <TextInput label={`${currencySymbol()}/mo`} type="number" onChange={(e) => setF({ ...f, monthly_contribution: parseFloat(e.target.value) || 0 })} />
          <TextInput label={`Already saved ${currencySymbol()}`} type="number" onChange={(e) => setF({ ...f, opening_balance: parseFloat(e.target.value) || 0 })} />
          <TextInput label="Since" type="date" value={f.as_of_date} onChange={(e) => setF({ ...f, as_of_date: e.target.value })} />
          <TextInput label={`Covers ${currencySymbol()}/yr`} type="number" onChange={(e) => setF({ ...f, target_annual: parseFloat(e.target.value) || null })} />
        </FormRow>
      </AddPanel>
      )}

      <p className="text-xs text-ink-3">
        Sinking funds smooth lumpy costs: set aside a fixed amount each month, the balance grows, and you log a withdrawal when
        you spend (e.g. book tickets). Balances count under Net Worth (Cash &amp; Bank); the monthly set-aside
        feeds the Overview engine.
      </p>
    </div>
  )
}
