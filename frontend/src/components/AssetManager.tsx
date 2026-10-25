import { Fragment, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchNetworth,
  createAsset,
  updateAsset,
  deleteAsset,
  disposeAsset,
  formatINR,
  currencySymbol,
  displayCurrency,
  type AssetInput,
  type Asset,
  type DisposalInput,
} from '../api/client'
import { SaleForm } from './Disposals'
import {
  Loading,
  ErrorState,
  StatCard,
  StatGrid,
  RecordRow,
  FormRow,
  SubmitButton,
  TextInput,
  SelectInput,
  AddPanel,
  type RecordField,
} from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

const TYPES = ['stock', 'gold', 'vehicle', 'cash', 'property', 'other']

// A new asset is priced in the household's currency by default; a foreign holding overrides it.
const empty = (): AssetInput => ({ name: '', asset_type: 'stock', holder: 'Household', ticker: '', quantity: null, currency: displayCurrency(), manual_value_inr: null, meta: {} })

function EditRow({ a, onDone }: { a: Asset; onDone: () => void }) {
  const [qty, setQty] = useState(a.quantity ?? 0)
  const [val, setVal] = useState(a.manual_value_inr ?? 0)
  const [meta, setMeta] = useState<Record<string, any>>(a.meta ?? {})
  const save = useMutation({
    mutationFn: () => updateAsset(a.id, {
      name: a.name, asset_type: a.asset_type, holder: a.holder, ticker: a.ticker,
      quantity: qty, currency: a.currency, manual_value_inr: val, meta, notes: a.notes,
    }),
    onSuccess: onDone,
  })
  const m = (k: string, v: any) => setMeta({ ...meta, [k]: v })
  return (
    <FormRow
      cols={3}
      onSubmit={(e) => e.preventDefault()}
      submit={
        <>
          <button type="button" onClick={onDone} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
          <SubmitButton type="button" disabled={save.isPending} onClick={() => save.mutate()}>Save</SubmitButton>
        </>
      }
    >
      {(a.asset_type === 'stock' || a.asset_type === 'gold') && (
        <TextInput label={a.asset_type === 'gold' ? 'grams' : 'units'} type="number" value={qty} onChange={(e) => setQty(parseFloat(e.target.value) || 0)} />
      )}
      {a.asset_type === 'gold' && (
        <SelectInput label="Purity" value={meta.purity ?? '22k'} onChange={(e) => m('purity', e.target.value)}>
          <option value="24k">24k</option><option value="22k">22k</option><option value="18k">18k</option>
        </SelectInput>
      )}
      {(a.asset_type === 'vehicle' || a.asset_type === 'property') && (
        <>
          <TextInput label={`Purchase ${currencySymbol()}`} type="number" value={meta.purchase_price ?? ''} onChange={(e) => m('purchase_price', parseFloat(e.target.value) || null)} />
          <TextInput label="Purchase date" type="date" value={meta.purchase_date ?? ''} onChange={(e) => m('purchase_date', e.target.value || null)} />
        </>
      )}
      {a.asset_type === 'property' && (
        <TextInput label="Appreciation %/yr" type="number" step="0.5" value={meta.appreciation_pct ?? 7} onChange={(e) => m('appreciation_pct', parseFloat(e.target.value) || 0)} />
      )}
      {(a.asset_type === 'cash' || a.asset_type === 'other') && (
        <TextInput label={`Value ${currencySymbol()}`} type="number" value={val} onChange={(e) => setVal(parseFloat(e.target.value) || 0)} />
      )}
    </FormRow>
  )
}

function SellRow({ a, onDone }: { a: Asset; onDone: () => void }) {
  const sell = useMutation({
    mutationFn: (body: DisposalInput) => disposeAsset(a.id, body),
    onSuccess: onDone,
  })
  return <SaleForm holding={a.quantity} label={`Record sale of ${a.name}`} busy={sell.isPending}
                   error={sell.error} onCancel={onDone} onSubmit={(b) => sell.mutate(b)} />
}

export default function AssetManager() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['networth', refreshing],
    queryFn: () => fetchNetworth(refreshing),
  })
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('wealth', 'edit')

  const [f, setF] = useState<AssetInput>(empty)
  const create = useMutation({
    mutationFn: () => createAsset(f),
    onSuccess: () => { setF(empty()); refresh() },
  })
  const remove = useMutation({ mutationFn: (id: number) => deleteAsset(id), onSuccess: refresh })
  const [editId, setEditId] = useState<number | null>(null)
  const [sellId, setSellId] = useState<number | null>(null)

  if (isLoading) return <Loading rows={3} />
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />
  const assets = data.assets ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="tracked text-[11px] text-ink-3">Stocks priced live; cash / property / other use a value you set</div>
        {/* Re-pricing is a read. Hiding it would take a number away from someone allowed to see it. */}
        <button
          onClick={async () => { setRefreshing(true); await fetchNetworth(true); qc.invalidateQueries({ queryKey: ['networth'] }); setRefreshing(false) }}
          className="min-h-11 rounded-input border border-line px-3 text-[11px] tracked text-ink-2 transition-colors hover:border-accent hover:text-accent"
        >
          Refresh prices
        </button>
      </div>

      <StatGrid min="10rem">
        <StatCard label="Market & other assets" value={data?.total_inr ?? 0} tone="accent" />
      </StatGrid>

      <div className="space-y-3">
        {assets.map((a) => {
          // A sale's unreceived proceeds ride in this list as a synthetic line with no asset id —
          // it is money owed, so it has nothing to edit, delete or sell. Correct it under Sales.
          if (a.disposal_id) {
            return (
              <RecordRow
                key={`receivable-${a.disposal_id}`}
                title={a.name}
                subtitle={a.note ?? 'proceeds receivable'}
                fields={[{ label: 'Owed to you', value: formatINR(a.value_inr), mono: true, tone: 'accent' }]}
              />
            )
          }
          const holding = a.asset_type === 'stock' && a.ticker ? `${a.quantity} × ${a.unit_price ?? '?'} ${a.currency}`
            : a.asset_type === 'gold' ? `${a.quantity}g ${a.meta?.purity ?? ''} @ ${a.unit_price != null ? formatINR(a.unit_price) : '?'}/g`
            : a.asset_type === 'vehicle' ? (a.as_of ?? 'depreciated')
            : '—'
          const fields: RecordField[] = [
            { label: 'Holding', value: holding, mono: true },
            {
              label: 'Value',
              mono: true,
              value: <>{formatINR(a.value_inr)}<span className="ml-1.5 text-[9px] tracked text-ink-3">{a.priced}</span></>,
            },
          ]
          return (
            <Fragment key={a.id}>
              <RecordRow
                title={a.name}
                subtitle={`${a.asset_type}${a.holder ? ` · ${a.holder}` : ''}`}
                fields={fields}
                actions={mayEdit ? (
                  <>
                    <button onClick={() => setEditId(editId === a.id ? null : a.id)} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Edit</button>
                    <button onClick={() => { setSellId(sellId === a.id ? null : a.id); setEditId(null) }} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Record sale</button>
                    <button onClick={() => { if (confirm(`Delete ${a.name}?`)) remove.mutate(a.id) }} className="min-h-11 px-2 text-xs font-semibold tracked text-negative/70 transition-colors hover:text-negative">Delete</button>
                  </>
                ) : null}
              />
              {editId === a.id && <EditRow a={a} onDone={() => { setEditId(null); refresh() }} />}
              {sellId === a.id && <SellRow a={a} onDone={() => { setSellId(null); refresh() }} />}
            </Fragment>
          )
        })}
      </div>

      {/* add asset */}
      {!mayEdit ? <ViewOnly what="what this household owns" /> : (
      <AddPanel label="Add asset">
      <FormRow
        cols={4}
        onSubmit={(e) => e.preventDefault()}
        submit={
          <SubmitButton type="button" disabled={create.isPending || !f.name} onClick={() => create.mutate()}>Add asset</SubmitButton>
        }
      >
        <TextInput label="Name" placeholder="e.g. HDFC stock" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <SelectInput label="Type" value={f.asset_type} onChange={(e) => setF({ ...f, asset_type: e.target.value })}>
          {TYPES.map((t) => <option key={t}>{t}</option>)}
        </SelectInput>
        {f.asset_type === 'stock' ? (
          <>
            <TextInput label="Ticker" placeholder="GLE.PA" value={f.ticker ?? ''} onChange={(e) => setF({ ...f, ticker: e.target.value })} />
            <TextInput label="Qty" type="number" onChange={(e) => setF({ ...f, quantity: parseFloat(e.target.value) || null })} />
            <TextInput label="Currency" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} />
          </>
        ) : f.asset_type === 'gold' ? (
          <>
            <TextInput label="Grams" type="number" onChange={(e) => setF({ ...f, quantity: parseFloat(e.target.value) || null })} />
            <SelectInput label="Purity" value={f.meta?.purity ?? '22k'} onChange={(e) => setF({ ...f, meta: { ...f.meta, purity: e.target.value } })}>
              <option value="24k">24k</option><option value="22k">22k</option><option value="18k">18k</option>
            </SelectInput>
          </>
        ) : f.asset_type === 'vehicle' ? (
          <>
            <TextInput label={`Purchase ${currencySymbol()}`} type="number" onChange={(e) => setF({ ...f, meta: { ...f.meta, purchase_price: parseFloat(e.target.value) || null } })} />
            <TextInput label="Purchase date" type="date" onChange={(e) => setF({ ...f, meta: { ...f.meta, purchase_date: e.target.value || null } })} />
          </>
        ) : (
          <TextInput label={`Value ${currencySymbol()}`} type="number" onChange={(e) => setF({ ...f, manual_value_inr: parseFloat(e.target.value) || null })} />
        )}
      </FormRow>
      </AddPanel>
      )}

      <p className="text-xs text-ink-3">
        Stocks priced live (qty × price × FX→INR); cash/property/other use a value you set.
        EPF, PPF, FD and RD live under Investments above — not here.
        {mayEdit && ' Sold something? “Record sale” keeps the price, the date and the gain, and anything still owed to you stays on your net worth.'}
        {' '}Sales are listed under Sales &amp; capital gains below.
      </p>
    </div>
  )
}
