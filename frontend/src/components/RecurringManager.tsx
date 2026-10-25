import { Fragment, useEffect, useState, type ChangeEvent } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchRecurring,
  createRecurring,
  updateRecurring,
  contributeRecurring,
  deleteContribution,
  searchSchemes,
  fetchWallet,
  deleteRecurring,
  formatINR,
  currencySymbol,
  type RecurringInput,
  type RecurringInvestment,
  type RecurringPurpose,
} from '../api/client'
import InfoTip from './InfoTip'
import {
  Loading,
  StatGrid,
  StatCard,
  RecordRow,
  FormRow,
  SubmitButton,
  TextInput,
  SelectInput,
  HolderOptions,
  AddPanel,
  EmptyState,
  useConfirm,
  type RecordField, ErrorState,
} from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

const KINDS = ['sip', 'rd', 'fd', 'epf', 'ppf']
/** Exported so the Investments ledger groups by the same names the forms here offer. */
export const KIND_LABEL: Record<string, string> = {
  sip: 'SIP', rd: 'Recurring Deposit', fd: 'Fixed Deposit', epf: 'EPF', ppf: 'PPF',
}

const PURPOSES: { value: RecurringPurpose; label: string }[] = [
  { value: 'wealth', label: 'Build wealth' },
  { value: 'donation', label: 'Committed giving' },
]

/** One place both forms describe what Purpose does, so the two never drift apart. */
const PURPOSE_HINT =
  'Committed giving still debits your bank every month, but the money is already spoken for — it stays out of your wealth.'

const EMPTY: RecurringInput = {
  name: '', kind: 'epf', purpose: 'wealth', holder: 'Household', opening_balance: 0, as_of_date: '',
  monthly_contribution: 0, employer_contribution: 0, annual_rate_pct: null,
  compounding: null, maturity_date: null, active: true, from_takehome: false,
  scheme_code: null, units: null,
}

/** A scheme code alone is thrown away by the server — it needs units, or a value to back units out of.
 *  Blocking the save is the whole point: silently dropping the link is what left every fund on the
 *  assumed rate while the screen looked correctly filled in. */
const LINK_NEEDS_HOLDING =
  'Pick the fund AND give either its units or its current value. A fund code on its own is discarded, and the value stays an estimate.'
function unlinkableFund(f: RecurringInput): boolean {
  return f.kind === 'sip' && Boolean(f.scheme_code) && !f.units && !f.current_value
}

function PurposeSelect({ value, onChange }: { value: RecurringPurpose; onChange: (v: RecurringPurpose) => void }) {
  return (
    <SelectInput label="Purpose" value={value} onChange={(e) => onChange(e.target.value as RecurringPurpose)}>
      {PURPOSES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
    </SelectInput>
  )
}

// Full investment → editable input (so edit/pause send every field, wiping nothing).
function rowToInput(it: RecurringInvestment): RecurringInput {
  return {
    name: it.name, kind: it.kind, purpose: it.purpose ?? 'wealth', holder: it.holder ?? 'Household',
    opening_balance: it.opening_balance, as_of_date: it.as_of_date,
    monthly_contribution: it.monthly_contribution, employer_contribution: it.employer_contribution,
    annual_rate_pct: it.annual_rate_pct, compounding: it.compounding,
    maturity_date: it.maturity_date, active: it.active, from_takehome: it.from_takehome,
    scheme_code: it.scheme_code ?? null, units: it.units ?? null,
  }
}

// Months since a date (for the EPF/PPF re-anchor nudge).
function monthsSince(iso: string): number {
  const d = new Date(iso); const n = new Date()
  return (n.getFullYear() - d.getFullYear()) * 12 + (n.getMonth() - d.getMonth())
}

/** The segmented picker used for money-source and for ₹-vs-units. Same chip idiom as before. */
function Segmented<T extends string>({ value, onChange, options }: {
  value: T
  onChange: (v: T) => void
  options: { k: T; label: string; disabled?: boolean }[]
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={o.k}
          type="button"
          disabled={o.disabled}
          onClick={() => onChange(o.k)}
          className={`min-h-11 rounded-input border px-3 text-xs font-semibold transition disabled:opacity-40 ${
            value === o.k ? 'border-accent bg-accent-soft text-accent' : 'border-line text-ink-2 hover:text-ink'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * Money in and money out are two NAMED actions. A redemption used to be a minus sign typed into a
 * box labelled "top-up" — which is how the fund total drifted above what the broker actually holds.
 * Out can be entered in rupees or in units, because that is how a broker reports it.
 */
function MoneyPanel({ it, onDone }: { it: RecurringInvestment; onDone: () => void }) {
  const qc = useQueryClient()
  const mayEdit = useCan('wealth', 'edit')
  const [mode, setMode] = useState<'add' | 'redeem'>('add')
  const [unit, setUnit] = useState<'money' | 'units'>('money')
  const [amount, setAmount] = useState('')
  const [dt, setDt] = useState('')
  // Same rule as loan payments: default to the bank account, never the wallet.
  const [fundedFrom, setFundedFrom] = useState<'bank' | 'wallet' | 'other'>('bank')
  const w = useQuery({ queryKey: ['wallet'], queryFn: fetchWallet })
  const walletBal = w.data?.balance ?? 0
  const redeem = mode === 'redeem'
  // Units only price against a live NAV, so they need the scheme link first.
  const canUseUnits = Boolean(it.scheme_code)
  const inUnits = redeem && unit === 'units' && canUseUnits
  const typed = Number(amount)
  const n = Math.abs(typed)
  // Typing a minus into "Add money" is the old trap — say where that entry belongs instead.
  const wrongSign = !redeem && typed < 0
  const overWallet = !redeem && fundedFrom === 'wallet' && n > walletBal
  const blocked = wrongSign || overWallet
  const swapMode = (m: 'add' | 'redeem') => {
    setMode(m)
    // A redemption is money coming BACK, so the wallet can never be its source.
    if (m === 'redeem' && fundedFrom === 'wallet') setFundedFrom('bank')
    if (m === 'add') setUnit('money')
  }
  const post = useMutation({
    mutationFn: () => contributeRecurring(it.id, {
      ...(inUnits ? { units: redeem ? -n : n } : { amount: redeem ? -n : n }),
      date: dt, funded_from: fundedFrom,
    }),
    onSuccess: () => {
      setAmount(''); setDt(''); onDone()
      qc.invalidateQueries({ queryKey: ['wallet'] })
    },
  })
  const valueLabel = inUnits ? 'Units redeemed' : redeem ? `Amount redeemed ${currencySymbol()}` : `Amount ${currencySymbol()}`

  if (!mayEdit) return <ViewOnly what="the money going in and out of this investment" />

  return (
    <div className="space-y-3">
      <Segmented
        value={mode}
        onChange={swapMode}
        options={[{ k: 'add', label: 'Add money' }, { k: 'redeem', label: 'Record a redemption' }]}
      />
      {redeem && (
        <div>
          <div className="mb-1.5 tracked text-[11px] text-ink-2">Reported as</div>
          <Segmented
            value={canUseUnits ? unit : 'money'}
            onChange={setUnit}
            options={[
              { k: 'money', label: `Rupees ${currencySymbol()}` },
              { k: 'units', label: 'Units', disabled: !canUseUnits },
            ]}
          />
          {!canUseUnits && (
            <p className="mt-1.5 text-[11px] text-ink-3">
              Units need a scheme to price them — link an AMFI fund in Edit and units become available here.
            </p>
          )}
        </div>
      )}
      <FormRow
        cols={2}
        onSubmit={(e) => e.preventDefault()}
        submit={
          <SubmitButton
            type="button"
            disabled={post.isPending || !n || !dt || blocked}
            onClick={() => post.mutate()}
          >
            {redeem ? '− record redemption' : '+ add money'}
          </SubmitButton>
        }
      >
        <TextInput
          label={valueLabel}
          type="number"
          step={inUnits ? '0.0001' : undefined}
          placeholder={inUnits ? 'Units (e.g. 150.250)' : `Amount ${currencySymbol()}`}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        <TextInput label="Date" type="date" value={dt} onChange={(e) => setDt(e.target.value)} />
      </FormRow>
      <div>
        <div className="mb-1.5 tracked text-[11px] text-ink-2">{redeem ? 'Money landed in' : 'Pay from'}</div>
        <Segmented
          value={fundedFrom}
          onChange={setFundedFrom}
          options={
            redeem
              ? [{ k: 'bank' as const, label: 'Bank account' }, { k: 'other' as const, label: 'Other' }]
              : [
                  { k: 'bank' as const, label: 'Bank account' },
                  { k: 'wallet' as const, label: `Wallet · ${formatINR(walletBal)}` },
                  { k: 'other' as const, label: 'Other' },
                ]
          }
        />
        {redeem && (
          <p className="mt-1.5 text-[11px] text-ink-3">
            A redemption is money coming back to you, so the wallet is not offered — the wallet only
            funds money going out.
          </p>
        )}
        {wrongSign && (
          <p className="mt-1.5 text-[11px] text-negative">
            A minus here is money coming out — switch to “Record a redemption” and enter it as a positive number.
          </p>
        )}
        {overWallet && (
          <p className="mt-1.5 text-[11px] text-negative">
            The wallet holds {formatINR(walletBal)} — pay the rest from another source.
          </p>
        )}
        {post.isError && (
          <p className="mt-1.5 text-[11px] text-negative">Couldn’t save that entry. Check the date and try again.</p>
        )}
      </div>
    </div>
  )
}

/**
 * The ledger. `contributions` always came back from the API and was never rendered, so a mistyped
 * entry could not be found, let alone removed. Index is the position in the array the API returned —
 * the server keeps it date-sorted, so render it in that order and never re-sort here.
 */
function Ledger({ it, onChanged }: { it: RecurringInvestment; onChanged: () => void }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const mayEdit = useCan('wealth', 'edit')
  const rows = it.contributions ?? []
  const del = useMutation({
    mutationFn: (index: number) => deleteContribution(it.id, index),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['wallet'] }); onChanged() },
  })
  return (
    <div>
      <div className="mb-1.5 tracked text-[11px] text-ink-2">
        Ledger · {rows.length} {rows.length === 1 ? 'entry' : 'entries'}
      </div>
      {rows.length === 0 ? (
        <EmptyState
          title="Nothing logged yet"
          hint={mayEdit
            ? 'Top-ups and redemptions you record land here, so a wrong one can be found and removed.'
            : 'Top-ups and redemptions land here as they are recorded.'}
        />
      ) : (
        <div className="rounded-xl border border-line bg-surface">
          {rows.map((c, i) => {
            // Units win when present: a units entry carries the direction, the rupee field may be derived.
            const signed = c.units != null ? c.units : c.amount ?? 0
            const out = signed < 0
            return (
              <div key={i} className="flex items-center gap-3 border-b border-line px-3 py-2 last:border-0">
                <span className={`shrink-0 rounded-chip border px-1.5 py-0.5 text-[9px] tracked ${
                  out ? 'border-negative/40 text-negative' : 'border-accent/40 text-accent'
                }`}>
                  {out ? 'OUT' : 'IN'}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="money truncate text-sm font-medium text-ink">
                    {c.units != null ? (
                      <>
                        {Math.abs(c.units)} units
                        {c.nav != null && <span className="ml-1 text-[11px] text-ink-3">× {formatINR(c.nav)}</span>}
                      </>
                    ) : (
                      formatINR(Math.abs(c.amount ?? 0))
                    )}
                  </div>
                  <div className="truncate text-[11px] text-ink-3">
                    {out ? 'Redeemed' : 'Added'} · {c.date}
                    {c.funded_from ? ` · ${c.funded_from}` : ''}{c.note ? ` · ${c.note}` : ''}
                  </div>
                </div>
                {mayEdit && (
                  <button
                    type="button"
                    disabled={del.isPending}
                    onClick={async () => {
                      const ok = await confirm({
                        title: out ? 'Remove this redemption?' : 'Remove this top-up?',
                        message: 'The entry is deleted and the value recomputed without it.',
                        confirmLabel: 'Remove', danger: true,
                      })
                      if (ok) del.mutate(i)
                    }}
                    className="min-h-11 shrink-0 px-2 text-xs font-semibold tracked text-negative/70 transition-colors hover:text-negative disabled:opacity-50"
                  >
                    Remove
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}
      {del.isError && <p className="mt-1.5 text-[11px] text-negative">Couldn’t remove that entry. Try again.</p>}
    </div>
  )
}

/**
 * Type the fund name, pick the match — the AMFI code becomes a lookup instead of something the owner
 * is expected to already know. Replaces the bare code box.
 */
function SchemeLookup({ code, onPick }: { code: string | null; onPick: (code: string | null) => void }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 300)
    return () => clearTimeout(t)
  }, [q])
  const ready = term.length >= 3
  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: ['schemes', term],
    queryFn: () => searchSchemes(term),
    enabled: ready,
  })
  const hits = data ?? []
  if (code) {
    return (
      <div className="sm:col-span-2 lg:col-span-4">
        <div className="mb-1.5 block text-xs font-semibold text-ink-2">Fund</div>
        <div className="flex min-h-11 items-center justify-between gap-3 rounded-input border border-line bg-surface px-3">
          <span className="money truncate text-sm text-ink">AMFI {code}</span>
          <button
            type="button"
            onClick={() => { onPick(null); setQ(''); setTerm('') }}
            className="min-h-11 shrink-0 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent"
          >
            Change
          </button>
        </div>
      </div>
    )
  }
  return (
    <div className="sm:col-span-2 lg:col-span-4">
      <TextInput
        label="Find the fund"
        placeholder="Type the fund name…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="mt-2">
        {!ready ? (
          <p className="text-[11px] text-ink-3">Type at least 3 letters of the fund name to search AMFI.</p>
        ) : isFetching ? (
          <Loading rows={2} label="Searching funds…" />
        ) : isError ? (
          <ErrorState message="Couldn’t reach the AMFI scheme list." onRetry={() => refetch()} />
        ) : hits.length === 0 ? (
          <EmptyState title="No fund matched" hint="Try a shorter part of the name — the fund house alone often works." />
        ) : (
          <div className="max-h-56 overflow-y-auto rounded-input border border-line bg-surface">
            {hits.map((s) => (
              <button
                key={s.scheme_code}
                type="button"
                onClick={() => onPick(s.scheme_code)}
                className="flex min-h-11 w-full items-center justify-between gap-3 border-b border-line px-3 py-2 text-left transition-colors last:border-0 hover:bg-surface-2"
              >
                <span className="min-w-0 truncate text-sm text-ink">{s.scheme_name}</span>
                <span className="money shrink-0 text-[11px] text-ink-3">{s.scheme_code}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * An un-linked fund's value is compounded at an ASSUMED rate. It used to render exactly like a
 * market-priced one, which is how a guess passed for a fact. This says so on the card itself — not
 * behind an expander — and offers the one tap that fixes it.
 */
function AssumedBadge({ onFix }: { onFix: () => void }) {
  const mayEdit = useCan('wealth', 'edit')
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warn/40 bg-warn/5 px-4 py-3">
      <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-ink-2">
        <span className="font-semibold text-warn">Estimate, not a market value.</span>{' '}
        No AMFI scheme is linked, so this value is the money you put in grown at the assumed rate
        shown — it is not what the fund is worth today. Link the scheme and units to price it live.
      </p>
      {mayEdit && (
        <button
          type="button"
          onClick={onFix}
          className="min-h-11 shrink-0 rounded-input border border-warn/50 px-3 text-xs font-semibold tracked text-warn transition-colors hover:bg-warn/10"
        >
          Link the fund
        </button>
      )}
    </div>
  )
}

function EditForm({ it, onDone }: { it: RecurringInvestment; onDone: () => void }) {
  const [f, setF] = useState<RecurringInput>({
    name: it.name, kind: it.kind, purpose: it.purpose ?? 'wealth', holder: it.holder ?? 'Household',
    opening_balance: it.opening_balance, as_of_date: it.as_of_date,
    monthly_contribution: it.monthly_contribution, employer_contribution: it.employer_contribution,
    annual_rate_pct: it.annual_rate_pct, compounding: it.compounding,
    maturity_date: it.maturity_date, active: it.active, from_takehome: it.from_takehome,
    scheme_code: it.scheme_code ?? null, units: it.units ?? null,
  })
  const save = useMutation({ mutationFn: () => updateRecurring(it.id, f), onSuccess: onDone })
  const mayEdit = useCan('wealth', 'edit')
  const num = (k: keyof RecurringInput) => (e: ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value === '' ? null : parseFloat(e.target.value) })
  const isEpf = f.kind === 'epf'
  const isMf = f.kind === 'sip'
  const linkIncomplete = unlinkableFund(f)
  const hasMaturity = f.kind === 'rd' || f.kind === 'ppf' || f.kind === 'fd'
  // What "start balance" means depends on type — this is the field that confused before.
  const startHint = f.kind === 'rd'
    ? 'Start balance = what was already in it on the start date — 0 for a fresh RD. The installments build the value; do NOT put the deposited-so-far here.'
    : isMf
    ? 'Start balance = amount invested as of the anchor date. Value comes from units × live NAV.'
    : 'Start balance = the balance on the As-of date. To re-anchor EPF/PPF, paste the latest passbook balance and set As-of to today.'

  if (!mayEdit) return <ViewOnly what="this investment" />

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-ink-2">
        Now: value <span className="money text-ink">{formatINR(it.current_value)}</span> ·
        contributed <span className="money text-ink">{formatINR(it.total_contributed)}</span> (both computed).
        <span className="ml-1 text-ink-3">{startHint}</span>
        <span className="ml-1 text-ink-3">{PURPOSE_HINT}</span>
      </div>
      <FormRow
        cols={4}
        onSubmit={(e) => e.preventDefault()}
        submit={
          <>
            <button type="button" onClick={onDone} className="min-h-11 rounded-input border border-line px-4 text-sm text-ink-2 transition-colors hover:text-ink">Cancel</button>
            <SubmitButton type="button" disabled={save.isPending || !f.name || !f.as_of_date || linkIncomplete} onClick={() => save.mutate()}>Save</SubmitButton>
          </>
        }
      >
        <TextInput label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <PurposeSelect value={f.purpose ?? 'wealth'} onChange={(purpose) => setF({ ...f, purpose })} />
        <SelectInput label="Holder" value={f.holder ?? 'Household'} onChange={(e) => setF({ ...f, holder: e.target.value })}>
          <HolderOptions />
        </SelectInput>
        <TextInput label={`Start bal ${currencySymbol()}`} type="number" value={f.opening_balance ?? 0} onChange={num('opening_balance')} />
        <TextInput label="as of" type="date" value={f.as_of_date} onChange={(e) => setF({ ...f, as_of_date: e.target.value })} />
        <TextInput label={`${currencySymbol()}/mo (self)`} type="number" value={f.monthly_contribution ?? 0} onChange={num('monthly_contribution')} />
        {isEpf && <TextInput label={`${currencySymbol()}/mo employer`} type="number" value={f.employer_contribution ?? 0} onChange={num('employer_contribution')} />}
        {!isMf && <TextInput label="Rate %" type="number" step="0.01" value={f.annual_rate_pct ?? ''} onChange={num('annual_rate_pct')} />}
        {isMf && <SchemeLookup code={f.scheme_code ?? null} onPick={(scheme_code) => setF({ ...f, scheme_code })} />}
        {isMf && <TextInput label="Units" type="number" step="0.0001" value={f.units ?? ''} onChange={num('units')} />}
        {hasMaturity && <TextInput label="matures" type="date" value={f.maturity_date ?? ''} onChange={(e) => setF({ ...f, maturity_date: e.target.value || null })} />}
        <label className="flex items-center gap-2 self-end pb-2 text-xs text-ink-2">
          <input type="checkbox" checked={f.from_takehome ?? true} onChange={(e) => setF({ ...f, from_takehome: e.target.checked })} /> debits bank
        </label>
        <label className="flex items-center gap-2 self-end pb-2 text-xs text-ink-2">
          <input type="checkbox" checked={f.active ?? true} onChange={(e) => setF({ ...f, active: e.target.checked })} /> active
        </label>
      </FormRow>
      {linkIncomplete && <p className="text-[11px] text-negative">{LINK_NEEDS_HOLDING}</p>}
    </div>
  )
}

export default function RecurringManager() {
  const qc = useQueryClient()
  const refresh = useHouseholdRefresh()
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['recurring'], queryFn: () => fetchRecurring() })
  const [f, setF] = useState<RecurringInput>(EMPTY)
  const [openId, setOpenId] = useState<number | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [navRefreshing, setNavRefreshing] = useState(false)
  const mayEdit = useCan('wealth', 'edit')
  const create = useMutation({ mutationFn: () => createRecurring(f), onSuccess: () => { setF(EMPTY); refresh() } })
  const remove = useMutation({ mutationFn: (id: number) => deleteRecurring(id), onSuccess: refresh })
  const pause = useMutation({
    mutationFn: (it: RecurringInvestment) => updateRecurring(it.id, { ...rowToInput(it), active: !it.active }),
    onSuccess: refresh,
  })
  const refreshNav = async () => {
    setNavRefreshing(true)
    await fetchRecurring(true)            // force live NAV re-fetch for all funds
    qc.invalidateQueries({ queryKey: ['recurring'] })
    qc.invalidateQueries({ queryKey: ['networth-summary'] })
    setNavRefreshing(false)
  }

  if (isLoading) return <Loading label="Loading investments…" />
  // Without this an error falls through and renders your investments as none.
  if (isError || !data) return <ErrorState message="Couldn’t load your investments." onRetry={() => refetch()} />
  const items = data?.investments ?? []
  // Value sitting in donation-purpose instruments: real money, but never part of the corpus.
  const givingValue = Math.round(((data?.total_value ?? 0) - (data?.total_wealth_value ?? 0)) * 100) / 100
  const giving = givingValue > 0 || (data?.donation_outflow ?? 0) > 0
  const isEpf = f.kind === 'epf'
  const isMf = f.kind === 'sip'
  const hasMaturity = f.kind === 'rd' || f.kind === 'ppf' || f.kind === 'fd'

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="tracked text-[11px] text-ink-3">Mutual-fund value is live; fixed-rate is computed</div>
        {/* Re-pricing is a read. Hiding it would take a number away from someone allowed to see it. */}
        <button onClick={refreshNav} disabled={navRefreshing}
          className="min-h-11 rounded-input border border-line px-3 text-[11px] tracked text-ink-2 transition-colors hover:border-accent hover:text-accent disabled:opacity-50">
          {navRefreshing ? 'Refreshing NAV…' : 'Refresh fund NAV'}
        </button>
      </div>

      <StatGrid min="10rem">
        <StatCard label={giving ? 'Wealth value' : 'Invested value'}
          value={giving ? (data?.total_wealth_value ?? 0) : (data?.total_value ?? 0)} tone="accent" />
        {giving && <StatCard label="Held for giving" value={givingValue} tone="warn" />}
        <StatCard label="Contributed" value={data?.total_contributed ?? 0} />
        <StatCard label="Gain so far" value={data?.total_interest ?? 0} />
        <StatCard label={giving ? 'Into wealth / mo' : 'From take-home / mo'}
          value={giving ? (data?.wealth_outflow ?? 0) : (data?.takehome_outflow ?? 0)} />
      </StatGrid>

      {giving && (
        <p className="text-[11px] leading-relaxed text-ink-3">
          + {formatINR(data?.donation_outflow ?? 0)}/mo committed giving
          <InfoTip text={PURPOSE_HINT} />
          <span className="ml-1">
            — money already spoken for never becomes wealth, but it still leaves your bank every month.
          </span>
        </p>
      )}

      {(data?.at_source_inflow ?? 0) > 0 && (
        <p className="text-[11px] leading-relaxed text-ink-3">
          + {formatINR(data?.at_source_inflow ?? 0)}/mo into EPF
          <InfoTip text="EPF is deducted before your salary reaches the bank and includes the employer match — it grows your retirement pot but never leaves your take-home, so it's not part of the monthly flow." />
          <span className="ml-1">at source (your share + employer match) — not from take-home.</span>
        </p>
      )}

      {/* investments — wide RecordRows fill the desktop band; detail expands full-width */}
      <div className="space-y-3">
        {items.length === 0 && (
          <EmptyState title="No investments tracked yet" hint="Add a SIP, RD, FD, EPF or PPF below and its value, contributions and gain start accruing here." />
        )}
        {items.map((it) => {
          // A fund is "really" priced only when it's linked to a live AMFI NAV. Otherwise its value is
          // a projection compounded at an ASSUMED return — label it honestly so a guess never looks
          // like a fact. (Link AMFI code + units via Edit to switch it to live NAV.)
          const marketPriced = Boolean(it.scheme_code && it.units && it.nav)
          const assumed = it.kind === 'sip' && !marketPriced
          const fields: RecordField[] = [
            {
              label: `${currencySymbol()}/mo`,
              mono: true,
              value: it.monthly_inflow ? (
                <>
                  {formatINR(it.monthly_inflow)}
                  {it.monthly_inflow > 0 && !it.from_takehome && (
                    <span className="ml-1 font-sans text-[9px] tracked text-ink-3">at source</span>
                  )}
                </>
              ) : '—',
            },
            { label: 'Contributed', value: formatINR(it.total_contributed), mono: true },
            {
              label: marketPriced ? 'Value · live NAV' : assumed ? 'Value · est' : 'Value',
              mono: true,
              value: (
                <>
                  {formatINR(it.current_value)}
                  <span className={`ml-2 text-[9px] tracked ${it.interest_earned >= 0 ? 'text-positive' : 'text-negative'}`}>
                    {it.interest_earned >= 0 ? '+' : ''}{formatINR(it.interest_earned)}
                  </span>
                </>
              ),
            },
            // Market-linked funds show the REAL return from live NAV: XIRR (money-weighted, annualised)
            // once there's ≥90d of history, else the simple absolute return so far. Everything else
            // shows its rate (assumed for un-linked SIPs). Groww-style honesty — never a fake number.
            marketPriced && it.xirr_pct != null
              ? { label: 'XIRR', mono: true, tone: it.xirr_pct >= 0 ? 'positive' : 'negative',
                  value: `${it.xirr_pct >= 0 ? '+' : ''}${it.xirr_pct}%` }
              : marketPriced && it.return_pct != null
              ? { label: 'Return', mono: true, tone: it.return_pct >= 0 ? 'positive' : 'negative',
                  value: `${it.return_pct >= 0 ? '+' : ''}${it.return_pct}%` }
              : {
                  label: assumed ? 'Rate · assumed' : 'Rate',
                  mono: true,
                  value: it.annual_rate_pct != null ? `${it.annual_rate_pct}%` : '—',
                },
            {
              label: 'Maturity',
              value: it.maturity_value ? (
                <>
                  <span className="money font-medium text-accent">{formatINR(it.maturity_value)}</span>
                  <span className="ml-1 text-[10px] tracked text-ink-3">on {it.maturity_date}</span>
                </>
              ) : '—',
            },
          ]
          return (
            <Fragment key={it.id}>
              <RecordRow
                title={
                  <span className="inline-flex items-center gap-2">
                    <button onClick={() => setOpenId(openId === it.id ? null : it.id)} className="inline-flex min-h-11 items-center text-left transition-colors hover:text-accent">{it.name}</button>
                    {!it.active && <span className="rounded-chip border border-line-2 px-1.5 py-0.5 text-[9px] tracked text-ink-3">PAUSED</span>}
                    {it.purpose === 'donation' && <span className="rounded-chip border border-line-2 px-1.5 py-0.5 text-[9px] tracked text-warn">GIVING</span>}
                    {assumed && <span className="rounded-chip border border-warn/50 px-1.5 py-0.5 text-[9px] tracked text-warn">ESTIMATE</span>}
                  </span>
                }
                subtitle={`${KIND_LABEL[it.kind] ?? it.kind} · ${it.holder ?? '—'}`}
                fields={fields}
                actions={mayEdit ? (
                  <>
                    <button onClick={() => pause.mutate(it)} disabled={pause.isPending}
                      className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">{it.active ? 'Pause' : 'Resume'}</button>
                    <button onClick={() => { setOpenId(it.id); setEditId(editId === it.id ? null : it.id) }} className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent">Edit</button>
                    <button onClick={() => { if (confirm(`Delete ${it.name}?`)) remove.mutate(it.id) }} className="min-h-11 px-2 text-xs font-semibold tracked text-negative/70 transition-colors hover:text-negative">Delete</button>
                  </>
                ) : null}
              />
              {/* Not behind the expander: an assumed number has to admit it on the card itself. */}
              {assumed && <AssumedBadge onFix={() => { setOpenId(it.id); setEditId(it.id) }} />}
              {openId === it.id && (
                <div className="space-y-3 rounded-2xl border border-line bg-surface-2 p-4">
                  {editId === it.id && <EditForm it={it} onDone={() => { setEditId(null); refresh() }} />}
                  <div className="text-[11px] text-ink-3">
                    {it.scheme_code && it.units ? (
                      <>AMFI {it.scheme_code} · {it.units} units × NAV {it.nav != null ? formatINR(it.nav) : '?'} ({it.priced}, {it.nav_date ?? '—'}) · invested {formatINR(it.opening_balance)}</>
                    ) : it.kind === 'sip' ? (
                      <>No scheme linked — use Edit to find the fund by name and set its units.</>
                    ) : (
                      <>Opening {formatINR(it.opening_balance)} as of {it.as_of_date} · {it.months_elapsed} months tracked · {it.compounding} compounding</>
                    )}
                  </div>
                  {(it.kind === 'epf' || it.kind === 'ppf') && it.active && monthsSince(it.as_of_date) >= 3 && (
                    <div className="text-[11px] text-warn">
                      ⚠ anchored {monthsSince(it.as_of_date)} months ago — re-anchor: open Edit, paste the latest passbook balance into Opening and set As-of to today.
                    </div>
                  )}
                  <MoneyPanel it={it} onDone={refresh} />
                  <Ledger it={it} onChanged={refresh} />
                </div>
              )}
            </Fragment>
          )
        })}
      </div>

      {/* add investment */}
      {!mayEdit ? <ViewOnly what="your investments" /> : (
      <AddPanel label="Add investment">
      <FormRow
        cols={4}
        onSubmit={(e) => e.preventDefault()}
        submit={
          <SubmitButton type="button" disabled={create.isPending || !f.name || !f.as_of_date || unlinkableFund(f)} onClick={() => create.mutate()}>Add</SubmitButton>
        }
      >
        <TextInput label="Name" placeholder="Name (e.g. EPF)" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <SelectInput label="Type" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value, from_takehome: e.target.value !== 'epf' })}>
          {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
        </SelectInput>
        <PurposeSelect value={f.purpose ?? 'wealth'} onChange={(purpose) => setF({ ...f, purpose })} />
        <SelectInput label="Holder" value={f.holder ?? 'Household'} onChange={(e) => setF({ ...f, holder: e.target.value })}>
          <HolderOptions />
        </SelectInput>
        <TextInput label={`Opening ${currencySymbol()}`} type="number" placeholder={`Opening ${currencySymbol()}`} onChange={(e) => setF({ ...f, opening_balance: parseFloat(e.target.value) || 0 })} />
        <TextInput label="as of" type="date" value={f.as_of_date} onChange={(e) => setF({ ...f, as_of_date: e.target.value })} />
        <TextInput label={`${currencySymbol()}/mo (self)`} type="number" placeholder={`${currencySymbol()}/mo (self)`} onChange={(e) => setF({ ...f, monthly_contribution: parseFloat(e.target.value) || 0 })} />
        {isEpf && <TextInput label={`${currencySymbol()}/mo employer`} type="number" placeholder={`${currencySymbol()}/mo employer`} onChange={(e) => setF({ ...f, employer_contribution: parseFloat(e.target.value) || 0 })} />}
        {!isMf && <TextInput label="Rate %" type="number" step="0.01" placeholder="Rate %" onChange={(e) => setF({ ...f, annual_rate_pct: parseFloat(e.target.value) })} />}
        {isMf && <SchemeLookup code={f.scheme_code ?? null} onPick={(scheme_code) => setF({ ...f, scheme_code })} />}
        {isMf && <TextInput label="Units" type="number" step="0.0001" placeholder="Units" onChange={(e) => setF({ ...f, units: parseFloat(e.target.value) || null })} />}
        {isMf && <TextInput label={`or Value ${currencySymbol()}`} type="number" placeholder={`or Value ${currencySymbol()}`} onChange={(e) => setF({ ...f, current_value: parseFloat(e.target.value) || null })} />}
        {hasMaturity && <TextInput label="matures" type="date" onChange={(e) => setF({ ...f, maturity_date: e.target.value || null })} />}
        <label className="flex items-center gap-2 self-end pb-2 text-xs text-ink-2">
          <input type="checkbox" checked={f.from_takehome ?? true} onChange={(e) => setF({ ...f, from_takehome: e.target.checked })} />
          debits bank
        </label>
      </FormRow>
      {unlinkableFund(f) && <p className="mt-2 text-[11px] text-negative">{LINK_NEEDS_HOLDING}</p>}
      </AddPanel>
      )}

      <p className="text-xs text-ink-3">
        Funds (SIP) are market-priced; RD/FD/EPF/PPF accrue from balance + rate. Pause skips a contribution;
        EPF is deducted at source (untick “debits bank”). {PURPOSE_HINT}
      </p>
    </div>
  )
}
