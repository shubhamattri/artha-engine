import { Fragment, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchInsurance,
  createInsurance,
  updateInsurance,
  deleteInsurance,
  formatINR,
  currencySymbol,
  type InsurancePolicy,
  type InsuranceInput,
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
  ErrorState,
  type RecordField,
} from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

export const KIND_LABEL: Record<string, string> = {
  term: 'Term', trop: 'Term + Return of Premium', endowment: 'Endowment', ulip: 'ULIP', health: 'Health',
}

const EMPTY: InsuranceInput = {
  provider: '', plan_name: null, policy_no: null, kind: 'term', holder: null, life_insured: null,
  sum_assured: 0, premium: 0, premium_frequency: 'monthly', start_date: null,
  policy_term_years: null, premium_paying_term_years: null, return_of_premium: false,
  maturity_date: null, surrender_value: null, active: true, notes: null,
}

// The edit form sends the whole policy back because PUT replaces every column — a field it did not
// carry over from the row would be saved as the default, not left alone.
const toInput = (p: InsurancePolicy): InsuranceInput => ({
  provider: p.provider, plan_name: p.plan_name, policy_no: p.policy_no, kind: p.kind,
  holder: p.holder, life_insured: p.life_insured, sum_assured: p.sum_assured, premium: p.premium,
  premium_frequency: p.premium_frequency, start_date: p.start_date,
  policy_term_years: p.policy_term_years, premium_paying_term_years: p.premium_paying_term_years,
  return_of_premium: p.return_of_premium, maturity_date: p.maturity_date,
  surrender_value: p.surrender_value, active: p.active, notes: p.notes,
})

/** Every field of a policy, in one grid, shared by the add panel and the per-row edit form. */
function PolicyFields({ f, set }: { f: InsuranceInput; set: (patch: Partial<InsuranceInput>) => void }) {
  return (
    <>
      <TextInput label="Provider" placeholder="e.g. the insurer" value={f.provider} onChange={(e) => set({ provider: e.target.value })} />
      <TextInput label="Plan" value={f.plan_name ?? ''} onChange={(e) => set({ plan_name: e.target.value || null })} />
      <TextInput label="Policy no." value={f.policy_no ?? ''} onChange={(e) => set({ policy_no: e.target.value || null })} />
      <SelectInput label="Type" value={f.kind} onChange={(e) => set({ kind: e.target.value })}>
        {Object.entries(KIND_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </SelectInput>
      <SelectInput label="Holder" value={f.holder ?? ''} onChange={(e) => set({ holder: e.target.value || null })}>
        <option value="">—</option>
        <HolderOptions />
      </SelectInput>
      <SelectInput label="Life insured" value={f.life_insured ?? ''} onChange={(e) => set({ life_insured: e.target.value || null })}>
        <option value="">—</option>
        <HolderOptions />
      </SelectInput>
      <TextInput label={`Cover ${currencySymbol()}`} type="number" value={f.sum_assured || ''} onChange={(e) => set({ sum_assured: parseFloat(e.target.value) || 0 })} />
      <TextInput label={`Premium ${currencySymbol()}`} type="number" value={f.premium || ''} onChange={(e) => set({ premium: parseFloat(e.target.value) || 0 })} />
      <SelectInput label="Paid" value={f.premium_frequency} onChange={(e) => set({ premium_frequency: e.target.value })}>
        <option value="monthly">Monthly</option>
        <option value="annual">Annually</option>
      </SelectInput>
      <TextInput label="Started" type="date" value={f.start_date ?? ''} onChange={(e) => set({ start_date: e.target.value || null })} />
      <TextInput label="Cover term (years)" type="number" value={f.policy_term_years ?? ''} onChange={(e) => set({ policy_term_years: parseInt(e.target.value, 10) || null })} />
      <TextInput label="Paying term (years)" type="number" value={f.premium_paying_term_years ?? ''} onChange={(e) => set({ premium_paying_term_years: parseInt(e.target.value, 10) || null })} />
      <SelectInput label="Money back at the end" value={f.return_of_premium ? 'yes' : 'no'} onChange={(e) => set({ return_of_premium: e.target.value === 'yes' })}>
        <option value="no">No — pure cover</option>
        <option value="yes">Yes — premiums returned</option>
      </SelectInput>
      <TextInput label="Matures" type="date" value={f.maturity_date ?? ''} onChange={(e) => set({ maturity_date: e.target.value || null })} />
      <TextInput label={`Surrender value ${currencySymbol()}`} type="number" value={f.surrender_value ?? ''} onChange={(e) => set({ surrender_value: parseFloat(e.target.value) || null })} />
      <SelectInput label="Status" value={f.active ? 'active' : 'lapsed'} onChange={(e) => set({ active: e.target.value === 'active' })}>
        <option value="active">Active</option>
        <option value="lapsed">Lapsed / closed</option>
      </SelectInput>
      <TextInput label="Notes" value={f.notes ?? ''} onChange={(e) => set({ notes: e.target.value || null })} />
    </>
  )
}

function EditRow({ p, onDone }: { p: InsurancePolicy; onDone: () => void }) {
  const [f, setF] = useState<InsuranceInput>(() => toInput(p))
  const save = useMutation({ mutationFn: () => updateInsurance(p.id, f), onSuccess: onDone })
  return (
    <FormRow
      cols={4}
      onSubmit={(e) => e.preventDefault()}
      submit={
        <>
          <button type="button" onClick={onDone} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
          <SubmitButton type="button" disabled={save.isPending || !f.provider} onClick={() => save.mutate()}>Save</SubmitButton>
        </>
      }
    >
      <PolicyFields f={f} set={(patch) => setF({ ...f, ...patch })} />
    </FormRow>
  )
}

export default function InsuranceManager() {
  const refresh = useHouseholdRefresh()
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['insurance'], queryFn: fetchInsurance })
  const del = useMutation({ mutationFn: (id: number) => deleteInsurance(id), onSuccess: refresh })
  const mayEdit = useCan('wealth', 'edit')

  const [f, setF] = useState<InsuranceInput>(EMPTY)
  const create = useMutation({ mutationFn: () => createInsurance(f), onSuccess: () => { setF(EMPTY); refresh() } })
  const [editId, setEditId] = useState<number | null>(null)

  if (isLoading) return <div className="text-sm text-ink-3">Loading insurance…</div>
  // A loading-or-missing guard swallows isError, so a failed fetch spins this skeleton forever
  // rather than saying it broke. States.tsx:5 warns about exactly this shape.
  if (isError || !data) return <ErrorState message="Couldn’t load your policies." onRetry={() => refetch()} />
  const policies = data.policies ?? []

  return (
    <div className="space-y-5">
      <StatGrid min="11rem">
        <StatCard label="Total life cover" value={data.total_cover} tone="accent" />
        <StatCard label="Annual premium" value={data.annual_premium} />
        <StatCard label="Cash value (net worth)" value={data.total_surrender_value} />
      </StatGrid>

      <div className="space-y-3">
        {policies.map((p) => {
          // Essentials only, aligned in fixed right-hanging slots. Type moves to the subtitle
          // chip (not a numeric slot); money-back shows just its value and only when it exists —
          // the maturity date + "pure cover" filler were noise.
          const fields: RecordField[] = [
            { label: 'Holder', value: p.holder ?? '—' },
            { label: 'Cover', value: formatINR(p.sum_assured), mono: true, tone: 'accent' },
            { label: 'Premium', value: `${formatINR(p.premium)}/${p.premium_frequency === 'monthly' ? 'mo' : 'yr'}`, mono: true },
          ]
          if (p.return_of_premium) {
            fields.push({ label: 'Money back', value: formatINR(p.maturity_benefit), mono: true, tone: 'positive' })
          }
          return (
            <Fragment key={p.id}>
              <RecordRow
                title={`${p.provider}${p.plan_name ? ` · ${p.plan_name}` : ''}`}
                subtitle={
                  <span className="inline-flex items-center gap-2">
                    <Chip>{KIND_LABEL[p.kind] ?? p.kind}</Chip>
                    {p.policy_no && <span>#{p.policy_no}</span>}
                  </span>
                }
                fields={fields}
                actions={mayEdit ? (
                  <>
                    <button onClick={() => setEditId(editId === p.id ? null : p.id)} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Edit</button>
                    <button onClick={() => { if (confirm(`Delete ${p.provider}?`)) del.mutate(p.id) }} className="min-h-11 px-2 text-xs font-semibold tracked text-negative/70 transition-colors hover:text-negative">Delete</button>
                  </>
                ) : null}
              />
              {editId === p.id && <EditRow p={p} onDone={() => { setEditId(null); refresh() }} />}
            </Fragment>
          )
        })}
      </div>

      {/* add policy */}
      {!mayEdit ? <ViewOnly what="these policies" /> : (
      <AddPanel label="Add policy">
        <FormRow
          cols={4}
          onSubmit={(e) => e.preventDefault()}
          submit={
            <SubmitButton type="button" disabled={create.isPending || !f.provider} onClick={() => create.mutate()}>Add policy</SubmitButton>
          }
        >
          <PolicyFields f={f} set={(patch) => setF({ ...f, ...patch })} />
        </FormRow>
      </AddPanel>
      )}

      <p className="text-xs text-ink-3">
        Pure term = protection (zero net-worth asset). Return-of-premium / endowment have a maturity value — set
        the surrender value to count it toward net worth.
      </p>
    </div>
  )
}
