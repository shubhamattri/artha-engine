import PayoffWaterfall from '../components/PayoffWaterfall'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchDebt,
  fetchDebtMoveNarration,
  fetchLoanDetail,
  createLoan,
  updateLoan,
  precloseLoan,
  deleteLoan,
  skipRecurring,
  withdrawSinking,
  addDisbursement,
  deleteDisbursement,
  addLoanPayment,
  fetchWallet,
  deleteLoanPayment,
  formatINR,
  shortINR,
  type Loan,
  type LoanInput,
  type DebtStrategy,
  type DebtRecoveryOption,
  currencySymbol,
  skipLoanEmi,
  type LoanPayment,
} from '../api/client'
import { ViewOnly, useCan } from '../auth/Can'
import {
  HolderOptions,
  PageContainer,
  PageGrid,
  SectionHeader,
  StatCard,
  Card,
  RecordRow,
  Chip,
  ErrorState,
  Sheet,
  FormRow,
  SubmitButton,
  TextInput,
  SelectInput,
  type RecordField,
  DetailCell,
} from '../components/ui'

const CATEGORY_LABEL: Record<string, string> = {
  Personal: 'Personal Loan', Car: 'Car Loan', Home: 'Home Loan',
  Education: 'Education Loan', 'Credit Card': 'Credit Card', 'Gold Loan': 'Gold Loan',
}

/** Human duration from a month count: 88 → "7y 4m", 70 → "5y 10m", 7 → "7 mo". */
/** "9 Apr 2024" — the day a loan was actually taken, not a month bucket. */
function takenOn(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtDur(months: number): string {
  const y = Math.floor(months / 12)
  const m = Math.round(months % 12)
  if (y === 0) return `${m} mo`
  if (m === 0) return `${y} yr${y > 1 ? 's' : ''}`
  return `${y}y ${m}m`
}

/** A concrete calendar month N months from today — "month 17" is abstract, "Feb 2028" is real. */
function clearDate(monthsFromNow: number): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() + monthsFromNow)
  return d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })
}

/** Small label/value pair used inside loan cards + detail (not the form Field). */

/** Savings vs paying only the EMIs. The API sends null (not 0) when the EMIs-only baseline never
 *  finishes — there is no finish line to measure against, so this renders "—" and says why. */
function SavingsVsEmis({ o, className = '' }: { o: DebtStrategy; className?: string }) {
  const known = o.baseline_complete && o.interest_saved != null && o.months_saved != null
  return (
    <div className={className}>
      <div className="grid grid-cols-2 gap-4">
        <DetailCell label="Interest saved vs EMIs only" value={known ? shortINR(o.interest_saved!) : '—'} accent={known} />
        <DetailCell label="Time saved vs EMIs only" value={known ? fmtDur(o.months_saved!) : '—'} accent={known} />
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-ink-3">
        {known
          ? 'Measured against paying every loan its own EMI and nothing more.'
          : 'Can’t be measured: paying only your EMIs never clears every loan, so there’s no finish line to compare this plan against.'}
      </p>
    </div>
  )
}

/** The answer to "which loan do I pay off first" — one plan, the reason it wins, what it saves.
 *  A null recommendation means no plan clears every loan; saying that beats inventing a pick. */
function RecommendedPlan({ rec, why, viewing, onUse }: {
  rec: DebtStrategy | null; why: string | null; viewing: boolean; onUse: () => void
}) {
  if (!rec) {
    return (
      <div className="rounded-2xl border border-warn/40 bg-warn/5 p-4">
        <div className="tracked text-[11px] font-bold text-warn">No recommendation yet</div>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">
          None of the plans below clears every loan inside the projection window, so there’s no honest
          “pay this one first” answer. Compare them anyway, or free up more spare cash and re-check.
        </p>
      </div>
    )
  }
  return (
    <div className="rounded-2xl border border-accent bg-accent-soft p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="tracked text-[11px] font-bold text-accent">Recommended · {rec.label}</div>
          <p className="mt-1 font-display text-lg font-bold leading-snug text-ink">{why ?? rec.description}</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {rec.complete
              ? <>Debt-free by <span className="font-semibold text-ink">{clearDate(rec.months)}</span> — {fmtDur(rec.months)} from now,
                  with <span className="money">{shortINR(rec.total_interest)}</span> of interest along the way
                  {(rec.prepayment_fees ?? 0) > 0 && (
                    <>, plus <span className="money">{shortINR(rec.prepayment_fees!)}</span> in charges to close
                    loans early that will not take part payments</>
                  )}.</>
              : <>Cheapest of the plans that get you there, though not every loan clears inside {fmtDur(rec.months)}.</>}
          </p>
        </div>
        {!viewing && (
          <button onClick={onUse}
            className="min-h-11 shrink-0 rounded-input border border-accent bg-surface px-3 text-xs font-semibold text-accent hover:bg-surface-2">
            Show this plan
          </button>
        )}
      </div>
      <SavingsVsEmis o={rec} className="mt-4 border-t border-accent/30 pt-3" />
      {/* only when the plan below isn't already carrying this warning for the same strategy */}
      {!viewing && (rec.never_amortises?.length ?? 0) > 0 && (
        <p className="mt-2 text-[11px] leading-relaxed text-warn">
          {rec.never_amortises.join(', ')} — the payment on {rec.never_amortises.length > 1 ? 'these' : 'this'} only
          covers the interest, so {rec.never_amortises.length > 1 ? 'they' : 'it'} never shrinks alone.
          {' '}{rec.never_amortises.length > 1 ? 'They clear' : 'It clears'} only because the rolled-down pool reaches
          {' '}{rec.never_amortises.length > 1 ? 'them' : 'it'}.
        </p>
      )}
    </div>
  )
}

function DisbursementPanel({ id, data }: { id: number; data: any }) {
  const qc = useQueryClient()
  // The payments panel below carries the one view-only sentence for this loan, so this panel just
  // drops its controls rather than saying the same thing twice on one screen.
  const mayEdit = useCan('debt', 'edit')
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['loan', id] })
    qc.invalidateQueries({ queryKey: ['debt'] })
  }
  const [amount, setAmount] = useState('')
  const [dt, setDt] = useState('')
  const [note, setNote] = useState('')
  const add = useMutation({
    mutationFn: () => addDisbursement(id, { amount: Number(amount), date: dt, note }),
    onSuccess: () => { setAmount(''); setDt(''); setNote(''); refresh() },
  })
  const del = useMutation({ mutationFn: (i: number) => deleteDisbursement(id, i), onSuccess: refresh })
  const tranches: { amount: number; date: string; note?: string }[] = data.disbursements ?? []
  return (
    <div className="mb-5 rounded-2xl border border-accent/30 bg-accent-soft p-4 text-xs leading-relaxed text-ink-2">
      <p className="mb-3">
        <span className="font-semibold text-accent">Flexi · {data.phase}</span> — sanctioned {formatINR(data.sanctioned_amount ?? 0)},
        {' '}drawn so far <span className="money text-ink">{formatINR(data.total_disbursed ?? 0)}</span> across {tranches.length} tranche(s).
        {' '}{data.moratorium_months}-mo moratorium: paying <span className="money text-ink">{formatINR(data.monthly_interest ?? 0)}</span>/mo
        interest-only; full EMI <span className="money text-ink">{formatINR(data.post_moratorium_emi ?? 0)}</span>/mo after.
      </p>
      {tranches.length > 0 && (
        <div className="mb-3 space-y-2">
          {tranches.map((t, i) => {
            const fields: RecordField[] = [{ label: 'Amount', value: formatINR(t.amount), mono: true }]
            if (t.note) fields.push({ label: 'Note', value: t.note })
            return (
              <RecordRow
                key={i}
                title={t.date}
                fields={fields}
                actions={mayEdit ? (
                  <button
                    onClick={() => del.mutate(i)}
                    className="rounded-input px-2 py-1 text-xs font-semibold text-negative hover:bg-surface-2"
                  >
                    Remove
                  </button>
                ) : undefined}
              />
            )
          })}
        </div>
      )}
      {mayEdit && (
      <FormRow
        cols={3}
        onSubmit={(e) => e.preventDefault()}
        submit={
          <SubmitButton
            type="button"
            disabled={add.isPending || !amount || !dt}
            onClick={() => add.mutate()}
          >
            + disbursement
          </SubmitButton>
        }
      >
        <TextInput label={`Amount ${currencySymbol()}`} type="number" placeholder={`Amount ${currencySymbol()}`} value={amount} onChange={(e) => setAmount(e.target.value)} />
        <TextInput label="Date" type="date" value={dt} onChange={(e) => setDt(e.target.value)} />
        <TextInput label="Note" placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      </FormRow>
      )}
    </div>
  )
}

function PaymentsPanel({ id, data, isGold }: { id: number; data: any; isGold: boolean }) {
  const qc = useQueryClient()
  const mayEdit = useCan('debt', 'edit')
  const refresh = () => {
    ;['loan', 'debt', 'cashflow', 'networth', 'cockpit', 'health'].forEach((k) =>
      qc.invalidateQueries({ queryKey: k === 'loan' ? ['loan', id] : [k] }))
  }
  const [amount, setAmount] = useState('')
  const [dt, setDt] = useState(new Date().toISOString().slice(0, 10))
  const [note, setNote] = useState('')
  // Where the money comes from. Defaults to the bank account, never the wallet: a payment made
  // without choosing must not quietly drain a balance you did not decide to spend.
  const [fundedFrom, setFundedFrom] = useState<'bank' | 'wallet' | 'other'>('bank')
  const wallet = useQuery({ queryKey: ['wallet'], queryFn: fetchWallet })
  const walletBal = wallet.data?.balance ?? 0
  const overWallet = fundedFrom === 'wallet' && Number(amount) > walletBal
  const add = useMutation({
    mutationFn: () => addLoanPayment(id, {
      amount: Number(amount), date: dt, note, funded_from: fundedFrom,
    }),
    onSuccess: () => {
      setAmount(''); setNote(''); refresh()
      qc.invalidateQueries({ queryKey: ['wallet'] })
    },
  })
  const del = useMutation({ mutationFn: (pid: number) => deleteLoanPayment(id, pid), onSuccess: refresh })
  // Recording a MISSED EMI, the mirror of a prepayment. Without it the plan assumes every scheduled
  // EMI landed and shows a date earlier than reality.
  const skip = useMutation({
    mutationFn: () => skipLoanEmi(id, { date: dt }),
    onSuccess: () => refresh(),
  })
  const payments: LoanPayment[] = data.payments ?? []
  const interest = data.interest_due ?? 0
  return (
    <div className="mb-5 rounded-2xl border border-accent/30 bg-accent-soft p-4 text-xs leading-relaxed text-ink-2">
      {isGold ? (
      <p className="mb-3">
        <span className="font-semibold text-accent">Gold loan · interest-only bullet</span> — principal
        outstanding <span className="money text-ink">{formatINR(data.principal_outstanding ?? data.balance_inr)}</span>,
        interest ~<span className="money text-ink">{formatINR(data.monthly_interest ?? 0)}</span>/mo.
        {interest > 0 && <> Unpaid interest so far <span className="money text-negative">{formatINR(interest)}</span>.</>}
        {' '}The expected monthly payment is assumed paid — log a row only to override a month
        (a bigger part payment, an irregular amount, or 0 for a month you skipped). Renews yearly.
      </p>
      ) : (
      <p className="mb-3">
        <span className="font-semibold text-accent">Extra payments</span> — comes off principal; the
        schedule below reprices from the new balance.
      </p>
      )}
      {payments.length > 0 && (
        <div className="mb-3 space-y-2">
          {payments.map((p) => {
            const skipped = p.kind === 'skipped_emi'
            const fields: RecordField[] = [{
              label: skipped ? 'Missed' : 'Amount', value: formatINR(p.amount), mono: true,
              tone: skipped ? 'negative' : 'positive',
            }]
            if (p.note) fields.push({ label: 'Note', value: p.note })
            return (
              <RecordRow key={p.id} title={p.date} fields={fields}
                actions={mayEdit ? (
                  <button onClick={() => del.mutate(p.id)}
                    className="rounded-input px-2 py-1 text-xs font-semibold text-negative hover:bg-surface-2">
                    Remove
                  </button>
                ) : undefined} />
            )
          })}
        </div>
      )}
      {!mayEdit ? <ViewOnly what="the payments on this loan" /> : (
      <>
      <FormRow cols={3} onSubmit={(e) => e.preventDefault()}
        submit={
          <SubmitButton type="button" disabled={add.isPending || !amount || !dt || overWallet} onClick={() => add.mutate()}>
            + log payment
          </SubmitButton>
        }>
        <TextInput label={`Amount ${currencySymbol()}`} type="number" placeholder={`Amount ${currencySymbol()}`} value={amount} onChange={(e) => setAmount(e.target.value)} />
        <TextInput label="Date" type="date" value={dt} onChange={(e) => setDt(e.target.value)} />
        <TextInput label="Note" placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      </FormRow>
      {/* Pay from — the wallet holds what past cycles underspent. Showing the balance next to the
          option is the point: choosing a source you cannot cover should be visible before you submit,
          not a 400 afterwards. */}
      <div className="mt-3">
        <div className="mb-1.5 text-[11px] uppercase tracking-wide text-ink-2">Pay from</div>
        <div className="flex flex-wrap gap-2">
          {([
            { k: 'bank', label: 'Bank account' },
            { k: 'wallet', label: `Wallet · ${formatINR(walletBal)}` },
            { k: 'other', label: 'Other' },
          ] as const).map((o) => (
            <button
              key={o.k}
              type="button"
              onClick={() => setFundedFrom(o.k)}
              className={`rounded-input border px-3 py-1.5 text-xs font-semibold transition ${
                fundedFrom === o.k
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-line text-ink-2 hover:text-ink'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        {overWallet && (
          <p className="mt-1.5 text-[11px] text-negative">
            The wallet holds {formatINR(walletBal)} — pay the rest from another source.
          </p>
        )}
      </div>
      <button
        type="button"
        disabled={skip.isPending || !dt}
        onClick={() => skip.mutate()}
        className="mt-2 rounded-input border border-line px-3 py-1.5 text-xs font-semibold text-ink-2 hover:text-negative disabled:opacity-40"
      >
        {skip.isPending ? 'Recording…' : `I skipped this month's ${data.loan_type === 'gold' ? 'payment' : 'EMI'}`}
      </button>
      </>
      )}
    </div>
  )
}

/** disbursement + tenure months → the next yearly renewal, e.g. "Jul 2027". */
function renewLabel(disbursement: string, tenureMonths: number): string {
  const d = new Date(disbursement)
  d.setMonth(d.getMonth() + (tenureMonths || 12))
  return d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })
}

function loanToInput(l: any): LoanInput {
  return {
    name: l.name, lender: l.lender, holder: l.holder, category: l.category,
    original_principal: l.original_principal, annual_rate_pct: l.rate_pct,
    tenure_months: l.tenure_months, disbursement_date: l.disbursement_date,
    loan_type: l.loan_type ?? 'standard', rate_type: l.rate_type ?? 'fixed',
    prepayment: l.prepayment ?? null, prepayment_charge_pct: l.prepayment_charge_pct ?? null,
    beneficiary: (l as any).beneficiary ?? 'household',
    foir_exempt: !!l.foir_exempt, serviced_by_household: l.serviced_by_household !== false,
    expected_monthly_payment: l.expected_monthly_payment ?? null,
    disbursed_amount: l.disbursed_amount ?? null,
    moratorium_months: l.moratorium_months ?? 0,
    moratorium_interest_mode: l.moratorium_interest_mode ?? 'full',
  }
}

function LoanDetail({ id }: { id: number }) {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['loan', id], queryFn: () => fetchLoanDetail(id) })
  const [editing, setEditing] = useState(false)
  const mayEdit = useCan('debt', 'edit')
  if (isLoading) return <div className="p-5 text-sm text-ink-3">Loading detail…</div>
  // A loading-or-missing guard swallows isError, so a failed fetch spins this skeleton forever
  // rather than saying it broke. States.tsx:5 warns about exactly this shape.
  if (isError || !data) return <ErrorState message="Couldn’t load this loan." onRetry={() => refetch()} />
  const isGold = data.loan_type === 'gold'
  if (editing) return (
    <div className="border-t border-line bg-surface-2 p-4 sm:p-5">
      <AddLoanForm loanId={id} initial={loanToInput(data)} onClose={() => setEditing(false)} />
    </div>
  )
  return (
    <div className="border-t border-line bg-surface-2 p-4 sm:p-5">
      {mayEdit && (
        <div className="mb-3 flex justify-end">
          <button onClick={() => setEditing(true)} className="inline-flex min-h-11 items-center px-2 text-xs font-semibold text-accent hover:underline">
            Edit loan
          </button>
        </div>
      )}
      {data.loan_type === 'flexi' && <DisbursementPanel id={id} data={data} />}
      <PaymentsPanel id={id} data={data} isGold={isGold} />
      {isGold ? (
        /* gold: a clean, correct grid — no amortising months-left / closes-on / interest-over-life */
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <DetailCell label="Original principal" value={formatINR(data.original_principal)} />
          <DetailCell label="Rate" value={`${data.rate_pct}%`} accent />
          <DetailCell label="Disbursed" value={data.disbursement_date} />
          <DetailCell label="Principal outstanding" value={formatINR(data.principal_outstanding ?? data.balance_inr)} accent />
          <DetailCell label="Interest / mo" value={formatINR(data.monthly_interest ?? 0)} />
          <DetailCell label="You pay / mo" value={formatINR(data.emi_inr)} />
          <DetailCell label="Principal repaid" value={formatINR(data.principal_paid)} accent />
          <DetailCell label="Interest paid so far" value={formatINR(data.interest_paid)} />
          <DetailCell label="Renews (yearly)" value={renewLabel(data.disbursement_date, data.tenure_months)} />
        </div>
      ) : (
      <>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
        <DetailCell label="Original principal" value={formatINR(data.original_principal)} />
        <DetailCell label="Tenure" value={`${data.tenure_months} mo`} />
        <DetailCell label="Disbursed" value={data.disbursement_date} />
        <DetailCell label="Months paid / left" value={`${data.months_passed} / ${data.months_remaining}`} />
        <DetailCell label="Principal repaid" value={formatINR(data.principal_paid)} accent />
        <DetailCell label="Interest paid so far" value={formatINR(data.interest_paid)} />
        <DetailCell label="Interest over life" value={formatINR(data.total_interest_over_life)} />
        <DetailCell label="Next due" value={data.next_due ?? '—'} />
        <DetailCell label="Closes on" value={data.closes_on ? `${data.closes_on} · ${data.closes_in_months} mo left` : '—'} accent />
        <DetailCell label="Rate type" value={data.rate_type === 'floating' ? 'Floating (tenure flexes with rate)' : 'Fixed'} />
        <DetailCell
          label="Early repayment"
          value={
            (data as any).prepayment_rule === 'part'
              ? `Part payment allowed${(data as any).prepayment_charge_pct ? ` · ${(data as any).prepayment_charge_pct}% charge` : ' · no charge'}`
              : (data as any).prepayment_rule === 'foreclose_only'
                ? `Full closure only${(data as any).prepayment_charge_pct ? ` · ${(data as any).prepayment_charge_pct}% charge` : ''}`
                : 'Not allowed'
          }
          accent={(data as any).prepayment_rule === 'part'}
        />
      </div>
      {/* progress */}
      <div className="mt-5">
        <div className="mb-1 flex justify-between text-[11px] text-ink-3">
          <span>Repaid</span><span className="money text-ink-2">{data.progress_pct}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-chip bg-surface-2">
          <div className="h-1.5 rounded-chip bg-accent" style={{ width: `${data.progress_pct}%` }} />
        </div>
      </div>
      {/* Forward schedule — runs from TODAY'S balance, every row a real month, so you can pick the
          month to foreclose in and see what it costs. The origination schedule answers a question
          about the past; this answers the one actually being asked. */}
      {(data.forward_schedule?.length ?? 0) > 0 && (
      <details className="mt-5">
        <summary className="cursor-pointer text-[11px] font-semibold text-ink-2 hover:text-ink">
          Payoff schedule · {data.forward_schedule.length} months · pick a month to close
        </summary>
        <p className="mt-2 text-[11px] leading-relaxed text-ink-3">
          Principal only — add the lender&rsquo;s closure charge.
          {data.forward_schedule[0]?.truncated && (
            <span className="text-negative">
              {' '}At this payment the balance is still {formatINR(data.forward_schedule[data.forward_schedule.length - 1].balance)}
              {' '}after {data.forward_schedule.length} months, so it never clears — "saved" is measured
              against the last month shown, not against term.
            </span>
          )}
        </p>
        <div className="mt-3 max-h-[28rem] space-y-2 overflow-y-auto pr-1">
          {data.forward_schedule.map((r: any) => (
            <RecordRow
              key={r.n}
              title={r.label}
              fields={[
                { label: 'EMI', value: formatINR(r.emi), mono: true },
                { label: 'Principal', value: formatINR(r.principal), mono: true, tone: 'positive' },
                { label: 'Interest', value: formatINR(r.interest), mono: true, tone: 'negative' },
                { label: 'To close', value: formatINR(r.closing_cost), mono: true },
                { label: 'Saved', value: formatINR(r.interest_saved_if_closed), mono: true, tone: 'positive' },
              ]}
            />
          ))}
        </div>
      </details>
      )}
      </>
      )}
    </div>
  )
}

function LoanCard({ loan, isTarget }: { loan: Loan & { closed?: boolean; progress_pct?: number }; isTarget: boolean }) {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()
  const mayEdit = useCan('debt', 'edit')
  const refresh = () => { qc.invalidateQueries({ queryKey: ['debt'] }); qc.invalidateQueries({ queryKey: ['cashflow'] }) }
  const foreclose = useMutation({ mutationFn: () => precloseLoan(loan.id, 'foreclose'), onSuccess: refresh })
  const remove = useMutation({ mutationFn: () => deleteLoan(loan.id), onSuccess: refresh })

  const closed = (loan as any).closed
  return (
    <div className={`overflow-hidden rounded-2xl border bg-surface ${isTarget ? 'border-accent' : 'border-line'}`}>
      <button onClick={() => setOpen((o) => !o)} className="w-full p-4 text-left sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-display text-lg font-semibold text-ink">{loan.name}</div>
            <div className="mt-0.5 text-xs text-ink-3">
              {loan.lender} · {loan.holder}
              {closed && (loan as any).closed_on ? ` · closed ${(loan as any).closed_on}` : ''}
            </div>
          </div>
          {closed ? (
            <span className="shrink-0 rounded-chip border border-positive px-2 py-0.5 text-[10px] font-semibold text-positive">Closed</span>
          ) : isTarget ? (
            <span className="shrink-0 rounded-chip border border-accent bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-accent">Attack Next</span>
          ) : (loan as any).loan_type === 'gold' ? (
            <span className="shrink-0 rounded-chip border border-accent/60 bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-accent">Gold · flexible</span>
          ) : (loan as any).beneficiary === 'external' ? (
            <span className="shrink-0 rounded-chip border border-line-2 px-2 py-0.5 text-[10px] font-semibold text-ink-3">Serviced for someone else</span>
          ) : (loan as any).foir_exempt ? (
            <span className="shrink-0 rounded-chip border border-line-2 px-2 py-0.5 text-[10px] font-semibold text-ink-3">External</span>
          ) : null}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
          {/* Borrowed vs owed. A card showing only the balance hides how far along you are, and on a
              flexi loan it hides that the sanction and the drawn amount are different numbers —
              Rs 20L sanctioned against Rs 10L actually taken. Show what was really borrowed. */}
          <DetailCell
            label={(loan as any).total_disbursed != null ? 'Borrowed (drawn)' : 'Borrowed'}
            value={formatINR((loan as any).total_disbursed ?? loan.original_principal)}
          />
          <DetailCell label="Owed now" value={formatINR(loan.balance_inr)} accent />
          <DetailCell label="Rate" value={`${loan.rate_pct.toFixed(2)}%${loan.rate_type === 'floating' ? ' float' : ''}`} accent />
          <DetailCell
            label="Taken on"
            /* "ago" only while it is running. For a closed loan months_passed is how long it RAN,
               which is a different fact — saying "ago" about it was what made a prepaid loan read
               as taken 7 years ago. */
            value={loan.disbursement_date
              ? `${takenOn(loan.disbursement_date)}${loan.months_passed
                  ? ` · ${fmtDur(loan.months_passed)} ${(loan as any).closed_on ? 'run' : 'ago'}`
                  : ''}`
              : '—'}
          />
          <DetailCell label={(loan as any).loan_type === 'gold' ? 'You pay / mo' : 'EMI / mo'} value={formatINR(loan.emi_inr)} />
          {(loan as any).beneficiary === 'external' && (
            <DetailCell label="Payoff plan" value="Minimum only — principal isn't yours" />
          )}
          {(loan as any).loan_type === 'gold' ? (
            <DetailCell label="Type" value="Interest-only · renews yearly" />
          ) : (
            <DetailCell label="Ends (EMI only)" value={loan.closes_on ? `${loan.closes_on.slice(0, 7)} · ${loan.months_remaining}mo` : '—'} />
          )}
        </div>
        <div className="mt-4 flex items-center justify-between border-t border-line pt-3 text-xs">
          <Chip>{CATEGORY_LABEL[loan.category] ?? loan.category}</Chip>
          <span className="text-ink-3">{open ? 'Hide details ▲' : 'Details ▾'}</span>
        </div>
      </button>

      {open && <LoanDetail id={loan.id} />}

      {open && !closed && mayEdit && (
        <div className="flex flex-wrap gap-2 border-t border-line px-4 py-3 sm:px-5">
          <button
            onClick={() => { if (confirm(`Pre-close ${loan.name}? This marks it fully paid.`)) foreclose.mutate() }}
            className="min-h-11 rounded-input border border-line px-3 text-xs font-semibold text-ink-2 hover:border-accent hover:text-accent"
          >
            {foreclose.isPending ? 'Closing…' : 'Pre-close (foreclose)'}
          </button>
          <button
            onClick={() => { if (confirm(`Delete ${loan.name}? This removes it entirely.`)) remove.mutate() }}
            className="min-h-11 rounded-input border border-line px-3 text-xs font-semibold text-ink-3 hover:border-negative hover:text-negative"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

const EMPTY: LoanInput = {
  name: '', lender: '', holder: 'Household', category: 'Personal',
  original_principal: 0, annual_rate_pct: 0, tenure_months: 0, disbursement_date: '',
  loan_type: 'standard', disbursed_amount: null, moratorium_months: 0, moratorium_interest_mode: 'full',
  rate_type: 'fixed', foir_exempt: false, serviced_by_household: true, expected_monthly_payment: null,
  prepayment: null, prepayment_charge_pct: null, beneficiary: 'household',
}

function AddLoanForm({ onClose, initial, loanId }: { onClose: () => void; initial?: LoanInput; loanId?: number }) {
  const qc = useQueryClient()
  const mayEdit = useCan('debt', 'edit')
  const [f, setF] = useState<LoanInput>(initial ?? EMPTY)
  const isEdit = loanId != null
  const create = useMutation({
    mutationFn: () => (isEdit ? updateLoan(loanId!, f) : createLoan(f)),
    onSuccess: () => {
      ;['debt', 'cashflow', 'networth', 'cockpit', 'health', 'commitments-reconcile'].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] }))
      if (isEdit) qc.invalidateQueries({ queryKey: ['loan', loanId] })
      onClose()
    },
  })
  const set = (k: keyof LoanInput) => (e: any) => setF({ ...f, [k]: e.target.value })
  const num = (k: keyof LoanInput) => (e: any) => setF({ ...f, [k]: parseFloat(e.target.value) || 0 })

  // Every field here exists to be saved, so filling one in would only end in a refused Save.
  if (!mayEdit) return <ViewOnly what="the loans and the payoff plan" />

  return (
    <div className="mb-6 space-y-3">
      <div className="text-xs font-semibold text-accent">{isEdit ? 'Edit loan' : 'Add a loan'}</div>
      <FormRow
        cols={4}
        onSubmit={(e) => e.preventDefault()}
        submit={
          <>
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 rounded-input border border-line px-4 text-sm font-semibold text-ink-2 hover:text-ink"
            >
              Cancel
            </button>
            <SubmitButton
              type="button"
              disabled={create.isPending || !f.name || !f.original_principal || !f.tenure_months || !f.disbursement_date}
              onClick={() => create.mutate()}
            >
              {create.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Add loan'}
            </SubmitButton>
          </>
        }
      >
        <TextInput label="Name" placeholder="Name" value={f.name} onChange={set('name')} />
        <TextInput label="Lender" placeholder="Lender" value={f.lender} onChange={set('lender')} />
        <SelectInput label="Holder" value={f.holder} onChange={set('holder')}>
          <HolderOptions />
        </SelectInput>
        <SelectInput label="Category" value={f.category} onChange={set('category')}>
          <option>Personal</option><option>Car</option><option>Home</option><option>Education</option><option>Credit Card</option><option>Gold Loan</option>
        </SelectInput>
        <TextInput label={`Principal ${currencySymbol()}`} type="number" placeholder={`Principal ${currencySymbol()}`} value={f.original_principal || ''} onChange={num('original_principal')} />
        <TextInput label="Rate %" type="number" step="0.01" placeholder="Rate %" value={f.annual_rate_pct || ''} onChange={num('annual_rate_pct')} />
        <TextInput label={f.loan_type === 'gold' ? 'Renewal (months)' : 'Tenure (months)'} type="number" placeholder="months" value={f.tenure_months || ''} onChange={num('tenure_months')} />
        <TextInput label="Disbursed on" type="date" value={f.disbursement_date} onChange={set('disbursement_date')} />
        <SelectInput label="Rate type" value={f.rate_type} onChange={set('rate_type')}>
          <option value="fixed">Fixed rate</option>
          <option value="floating">Floating (tenure flexes)</option>
        </SelectInput>
        {/* Not the same question as who PAYS it. A loan in your name for a sibling is your EMI and
            their principal, so prepaying it moves your money to them. */}
        <SelectInput label="Who is this loan for?" value={f.beneficiary ?? 'household'} onChange={set('beneficiary')}>
          <option value="household">Me or my household</option>
          <option value="external">Someone else — I only service it</option>
        </SelectInput>
        {/* Left empty, this is derived from the rate type: RBI forbids a prepayment charge on a
            floating-rate loan to an individual. Only set it when the sanction letter disagrees. */}
        <SelectInput label="Early repayment" value={f.prepayment ?? ''} onChange={set('prepayment')}>
          <option value="">Work it out from the rate type</option>
          <option value="part">Part payment allowed</option>
          <option value="foreclose_only">Full closure only</option>
          <option value="none">No early repayment</option>
        </SelectInput>
        {f.prepayment === 'foreclose_only' || f.prepayment === 'part' ? (
          <TextInput label="Closure charge %" type="number" step="0.1" placeholder="e.g. 3"
            value={f.prepayment_charge_pct ?? ''} onChange={num('prepayment_charge_pct')} />
        ) : null}
        <SelectInput label="Loan type" value={f.loan_type} onChange={set('loan_type')}>
          <option value="standard">Standard EMI</option>
          <option value="flexi">Flexi / interest-only (moratorium)</option>
          <option value="gold">Gold loan (interest-only, flexible)</option>
        </SelectInput>
        {/* Two independent flags — a gold loan in a parent's name that you service is OFF FOIR but you
            PAY it, so it still hits your cash flow + net worth. A truly external loan is off both. */}
        <label className="flex items-start gap-2 self-center text-xs text-ink-2 sm:col-span-2">
          <input type="checkbox" className="mt-0.5" checked={!!f.foir_exempt}
            onChange={(e) => setF({ ...f, foir_exempt: e.target.checked })} />
          <span><span className="font-semibold text-ink">Off my FOIR</span> — it's in someone else's name
            (e.g. a parent's), so keep it out of my eligibility ratio &amp; payoff plan. Still pledges gold.</span>
        </label>
        <label className="flex items-start gap-2 self-center text-xs text-ink-2 sm:col-span-2">
          <input type="checkbox" className="mt-0.5" checked={f.serviced_by_household !== false}
            onChange={(e) => setF({ ...f, serviced_by_household: e.target.checked })} />
          <span><span className="font-semibold text-ink">I pay this EMI</span> — count it in my monthly
            cash flow &amp; net-worth liabilities. Untick only if someone else actually pays it.</span>
        </label>
        {f.loan_type === 'gold' && (
          <>
            <TextInput label={`Expected payment ${currencySymbol()}/mo`} type="number" placeholder="e.g. 8000"
              value={f.expected_monthly_payment ?? ''} onChange={num('expected_monthly_payment')} />
            <p className="self-center text-xs text-ink-3 sm:col-span-2 lg:col-span-3">
              Interest-only bullet: principal stays put, you pay interest (plus any extra to chip it), and
              renew yearly. Set <span className="font-medium text-ink-2">Principal</span> = amount borrowed,
              <span className="font-medium text-ink-2"> Tenure</span> = renewal months (12). Log actual
              payments — any amount, any date — from the loan's details after adding it.
            </p>
          </>
        )}
        {f.loan_type === 'flexi' && (
          <>
            <TextInput label={`Disbursed so far ${currencySymbol()}`} type="number" placeholder={`Disbursed so far ${currencySymbol()}`} onChange={num('disbursed_amount')} />
            <TextInput label="Moratorium (months)" type="number" placeholder="Moratorium (months)" onChange={num('moratorium_months')} />
            <SelectInput label="Moratorium interest" value={f.moratorium_interest_mode ?? 'full'} onChange={set('moratorium_interest_mode')}>
              <option value="full">Paying full interest</option>
              <option value="partial">Paying partial interest</option>
              <option value="none">Paying nothing (capitalises)</option>
            </SelectInput>
            <p className="self-center text-xs text-ink-3 sm:col-span-2 lg:col-span-4">
              Interest accrues on disbursed amount during moratorium; full EMI starts after.
            </p>
          </>
        )}
      </FormRow>
    </div>
  )
}

/** The market's mainstream payoff methods, compared side-by-side. Tap one to make it your default
 *  view — it's remembered and reorders the loan list + drives the "this month" action. */
function StrategyPicker({ options, selected, onSelect, extra, recommended }: {
  options: DebtStrategy[]; selected: string; onSelect: (k: string) => void; extra: number
  recommended: string | null
}) {
  // Every ordering pays the same total against the same debt, so the finish date is usually
  // identical across methods. Saying so stops the card reading as a broken number.
  const allSameMonths = options.length > 1 && new Set(options.map((o) => o.months)).size === 1
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {options.map((o) => {
        const sel = o.key === selected
        return (
          <button
            key={o.key}
            onClick={() => onSelect(o.key)}
            className={`rounded-2xl border p-4 text-left transition-colors ${
              sel ? 'border-accent bg-accent-soft' : 'border-line bg-surface hover:border-line-2'}`}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="font-display text-base font-semibold text-ink">{o.label}</span>
              <span className="flex shrink-0 flex-wrap justify-end gap-1">
                {o.key === recommended && (
                  <span className="rounded-chip border border-accent bg-accent-soft px-2 py-0.5 text-[10px] font-bold text-accent">Recommended</span>
                )}
                {sel && <span className="rounded-chip bg-accent px-2 py-0.5 text-[10px] font-bold text-white">Showing</span>}
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-3">{o.description}</p>
            <div className="mt-3 flex items-end justify-between border-t border-line pt-3">
              <div>
                <div className="text-[11px] font-medium text-ink-3">
                  All loans paid off in{allSameMonths && <span className="text-ink-3"> (same for every method)</span>}
                </div>
                {/* honour `complete`: a plan where some loan never clears must NOT read as a payoff date */}
                <div className="money text-sm font-bold text-ink">
                  {o.complete ? fmtDur(o.months) : `over ${fmtDur(o.months)}`}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[11px] font-medium text-ink-3">Interest you’ll pay</div>
                <div className="money text-sm font-bold text-ink">{shortINR(o.total_interest)}</div>
                {(o.prepayment_fees ?? 0) > 0 && (
                  <div className="text-[10px] text-ink-3">+{shortINR(o.prepayment_fees!)} closure charges</div>
                )}
              </div>
            </div>
            {o.current && o.current.months > o.months && (
              <div className="mt-2 text-[11px] font-medium text-ink-3">
                If you only spare {shortINR(o.current.extra)}/mo: {fmtDur(o.current.months)}
              </div>
            )}
            {/* null (not 0) when the "pay only the EMIs" comparison never finishes — print a dash and the
                reason, never a ₹0 that would read as "this plan saves you nothing" */}
            <div className="mt-1 text-[11px] font-medium text-ink-3">
              {o.baseline_complete && o.interest_saved != null && o.months_saved != null
                ? `Saves ${shortINR(o.interest_saved)} and ${fmtDur(o.months_saved)} vs paying only your EMIs`
                : 'Saved vs paying only your EMIs: — (EMIs alone never clear your debt)'}
            </div>
          </button>
        )
      })}
      <p className="text-[11px] leading-relaxed text-ink-3 sm:col-span-2 xl:col-span-4">
        All four assume {shortINR(extra)}/mo of spare cash, every month.
      </p>
    </div>
  )
}

/** Record an extra (prepayment) toward a loan — the monthly "attack" move, distinct from foreclosure.
 *  Backed by POST /debt/{id}/preclose mode=prepay, which adds to extra_principal + re-derives payoff. */
function RecordPaymentSheet({ pay, onClose }: { pay: { id: number; name: string; amount: number }; onClose: () => void }) {
  const qc = useQueryClient()
  const mayEdit = useCan('debt', 'edit')
  const [amt, setAmt] = useState(String(pay.amount || ''))
  const [busy, setBusy] = useState(false)
  const save = async () => {
    const n = parseFloat(amt)
    if (!n || n <= 0) return
    setBusy(true)
    try {
      await precloseLoan(pay.id, 'prepay', n)
      qc.invalidateQueries({ queryKey: ['debt'] })
      qc.invalidateQueries({ queryKey: ['cashflow'] })
      onClose()
    } finally {
      setBusy(false)
    }
  }
  return (
    <Sheet open onClose={onClose} title={`Record a payment · ${pay.name}`}>
      {!mayEdit ? <ViewOnly what="the loans and the payoff plan" /> : (
      <div className="space-y-4">
        <TextInput label={`Extra payment ${currencySymbol()}`} type="number" inputMode="decimal" autoFocus value={amt} onChange={(e) => setAmt(e.target.value)} />
        <p className="text-xs leading-relaxed text-ink-3">Adds an extra principal payment on top of this loan’s EMI and re-computes its balance + payoff date. (This is the monthly “attack” move — not a foreclosure, which lives in the loan’s details.)</p>
        <button onClick={save} disabled={busy || !parseFloat(amt)}
          className="min-h-11 w-full rounded-input bg-accent text-sm font-semibold text-white hover:bg-accent-2 disabled:opacity-50">
          {busy ? 'Recording…' : 'Record payment'}
        </button>
      </div>
      )}
    </Sheet>
  )
}

export default function Debt() {
  const debtQuery = useQuery({ queryKey: ['debt'], queryFn: () => fetchDebt() })
  const [adding, setAdding] = useState(false)
  const [payFor, setPayFor] = useState<{ id: number; name: string; amount: number } | null>(null)
  const qc = useQueryClient()
  const mayEdit = useCan('debt', 'edit')
  // This one button spends from a sinking fund OR pauses a SIP — never a loan — so it needs both of
  // those permissions, not the debt one.
  const mayPullLever = useCan('plan.lines', 'edit') && useCan('wealth', 'edit')
  const [leverBusy, setLeverBusy] = useState<number | null>(null)
  // one-tap recovery levers: pause the SIP(s) or draw from a sinking fund, then refresh EVERYTHING that
  // depends on household-month state (so the gap, cockpit, plan and health all update at once).
  async function applyLever(o: DebtRecoveryOption, i: number) {
    if (!o.action || leverBusy != null) return
    setLeverBusy(i)
    try {
      if (o.action.type === 'skip_sip') {
        // skip ONLY what's needed, largest SIP first — never pause more than the gap
        let remaining = o.amount
        for (const s of o.action.sips) {
          if (remaining <= 0) break
          const use = Math.min(remaining, s.amount)
          await skipRecurring(s.id, use)
          remaining -= use
        }
      } else await withdrawSinking(o.action.id, { amount: o.amount, date: new Date().toISOString().slice(0, 10), note: 'Cover over-budget cycle' })
      ;['debt', 'cashflow', 'recurring', 'sinking', 'cockpit', 'budget', 'insights', 'health', 'networth', 'debt-move-narration']
        .forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
    } finally {
      setLeverBusy(null)
    }
  }
  // null = the user has never picked, so follow the engine's recommendation rather than a hardcoded
  // default. An explicit pick is remembered and always wins.
  const [strategy, setStrategy] = useState<string | null>(() => localStorage.getItem('artha-debt-strategy'))
  const pickStrategy = (k: string) => { setStrategy(k); localStorage.setItem('artha-debt-strategy', k) }

  const loans = debtQuery.data?.loans ?? []
  const strategies = debtQuery.data?.strategies
  const options = strategies?.options ?? []
  const recommendedKey = strategies?.recommended ?? null
  const recommended = options.find((o) => o.key === recommendedKey) ?? null
  const sel = options.find((o) => o.key === (strategy ?? recommendedKey)) ?? options[0]
  const sc = strategies?.surplus_context
  // the ONE money move this cycle — engine picks it (deterministic headline shows instantly); the AI
  // narration is fetched separately so the page never blocks on the model.
  const move = strategies?.move
  const moveNarr = useQuery({
    queryKey: ['debt-move-narration'],
    queryFn: fetchDebtMoveNarration,
    staleTime: 60_000,
    enabled: !!move && move.kind !== 'on_track' && move.kind !== 'unknown',
  })

  // active loans reordered by the selected strategy; closed loans pulled into their own section.
  // Gold loans are interest-only + flexible → their own section, OUT of the amortising avalanche plan.
  const openLoan = (l: any) => !l.closed && l.balance_inr > 0
  // ATTACKABLE = every serviced loan (incl. a gold loan) — they share the strategy-ordered list and the
  // payoff plan, just like any other loan. Only a truly external loan (someone else pays it) sits apart.
  const activeLoans = loans.filter((l: any) => openLoan(l) && l.serviced_by_household !== false)
  const externalLoans = loans.filter((l: any) => openLoan(l) && l.serviced_by_household === false)
  const closedLoans = loans.filter((l: any) => l.closed || l.balance_inr <= 0)
  const orderIds = sel?.order.map((o) => o.id) ?? []
  // A loan serviced for someone else is deliberately absent from the plan order, and building the
  // list purely from that order dropped it off the screen while it still counted in the debt total.
  // Excluding a loan from the ATTACK ORDER is not the same as hiding it: it is still owed, its EMI
  // still leaves the account. Append anything the order does not name, so nothing owed is invisible.
  const ordered = orderIds.length
    ? ([
        ...orderIds.map((id) => activeLoans.find((l: any) => l.id === id)).filter(Boolean),
        ...activeLoans.filter((l: any) => !orderIds.includes(l.id)),
      ] as Loan[])
    : activeLoans
  // the loan to attack first = the strategy's TOP PRIORITY (order[0]) — avalanche = highest rate,
  // snowball = smallest balance, etc. (NOT the first loan to close on its own, which is amount-driven
  // and made every strategy look identical). The badge + banner key off this.
  const attackId = sel?.order[0]?.id ?? ordered[0]?.id

  return (
    <PageContainer>


      {debtQuery.isLoading ? (
        <div className="text-ink-3">Loading…</div>
      ) : debtQuery.isError ? (
        <ErrorState onRetry={() => debtQuery.refetch()} />
      ) : (
        <>
          {/* The total, the monthly interest and the loan list now live in Balance's DebtBlock,
              above this. What used to sit here was a dark #231F1C slab repeating the same
              total — the design removes it by name, because it was the one thing making
              Debt look like a different app. Everything below is what Debt uniquely does. */}

          {/* payoff strategy — compare every method, pick your default */}
          {options.length > 0 && (
            <section className="space-y-4">
              <SectionHeader level={2} title="Which loan to pay off first?"
                sub="Here's our answer, and the three alternatives so the call stays yours." />
              {/* The engine couldn't read your income/budget, so it has no idea what you can spare. Say so
                  instead of quietly planning with ₹0 spare, which reads as a confident (wrong) answer. */}
              {sc?.data_ok === false && (
                <div className="rounded-2xl border border-warn/40 bg-warn/5 p-3 text-xs leading-relaxed text-ink-2">
                  <span className="font-semibold text-warn">Plan may be off.</span> We couldn’t work out your
                  spare cash this cycle (your income, bills or budget didn’t load), so the timelines below
                  assume nothing extra goes to your loans. Check the Plan and Cash-flow tabs.
                </div>
              )}
              <RecommendedPlan rec={recommended} why={strategies?.recommended_why ?? null}
                viewing={!!recommended && sel?.key === recommended.key}
                onUse={() => recommended && pickStrategy(recommended.key)} />
              <SectionHeader level={2} title="Compare the four methods"
                sub="Same spare cash either way, only the order changes. Pick one and it becomes your default." />
              <StrategyPicker options={options} selected={sel?.key ?? 'avalanche'} onSelect={pickStrategy}
                extra={sc?.surplus ?? strategies?.extra_per_month ?? 0} recommended={recommendedKey} />
              {/* THIS MONTH — one move, chosen by the engine (deterministic numbers), phrased by AI.
                  The engine decides which kind: cover a real cash shortfall, attack debt with spare, hold,
                  or set up Plan. No per-situation hardcoding in the UI — it just renders the move. */}
              {move && move.kind !== 'on_track' && (
                <div className={`rounded-2xl border p-4 ${
                  move.kind === 'attack_debt' ? 'border-accent/30 bg-accent-soft'
                    : move.kind === 'cover_overspend' ? 'border-negative/30 bg-surface'
                    : 'border-line bg-surface'}`}>
                  <div className={`flex items-center gap-1.5 text-xs font-semibold tracked ${
                    move.kind === 'attack_debt' ? 'text-accent' : move.kind === 'cover_overspend' ? 'text-negative' : 'text-ink-3'}`}>
                    This month
                    {moveNarr.data?.ai && <span className="font-normal normal-case tracking-normal text-ink-3">✦ AI</span>}
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-ink">{moveNarr.data?.headline ?? move.headline}</p>

                  {/* cover a real cash shortfall — the recovery levers (real cash only) + prevention note */}
                  {move.kind === 'cover_overspend' && move.recovery && move.recovery.options.length > 0 && (
                    <div className="mt-3 border-t border-line pt-3">
                      <div className="mb-2 flex items-baseline justify-between gap-2">
                        <span className="text-xs font-semibold text-ink-2">Cover {shortINR(move.recovery.gap)} from real cash</span>
                        <span className={`text-[11px] font-semibold ${move.recovery.covered ? 'text-positive' : 'text-negative'}`}>
                          {move.recovery.covered ? 'no loan needed' : `borrow ${shortINR(move.recovery.shortfall)}`}
                        </span>
                      </div>
                      <div className="overflow-hidden rounded-xl border border-line">
                        {move.recovery.options.map((o, i) => (
                          <div key={i} className="flex items-center gap-3 border-b border-line px-3 py-2.5 last:border-0">
                            <div className="min-w-0 flex-1">
                              <div className={`text-[13px] font-semibold ${o.kind === 'borrow' ? 'text-negative' : 'text-ink'}`}>{o.title}</div>
                              <div className="truncate text-[11px] text-ink-3">
                                {o.sub}{o.kind !== 'borrow' && o.available > o.amount ? ` · ${shortINR(o.available)} available` : ''}
                              </div>
                            </div>
                            <div className={`money shrink-0 text-sm font-bold ${o.kind === 'borrow' ? 'text-negative' : 'text-ink'}`}>{shortINR(o.amount)}</div>
                            {o.action && mayPullLever && (
                              <button onClick={() => applyLever(o, i)} disabled={leverBusy != null}
                                className="shrink-0 rounded-full border border-accent px-3 py-1 text-[11px] font-bold text-accent transition-colors hover:bg-accent-soft disabled:opacity-40">
                                {leverBusy === i ? '…' : o.action.type === 'skip_sip' ? `Skip ${shortINR(o.amount)}` : `Draw ${shortINR(o.amount)}`}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                      {move.recovery.prevent && (
                        <p className="mt-2 text-[11px] leading-relaxed text-ink-3">
                          It came from overspending <span className="money">{shortINR(move.recovery.prevent.total)}</span> ({move.recovery.prevent.sub}). That money’s already gone — reining it in won’t pay today’s gap, but keeps next cycle clear.
                        </p>
                      )}
                    </div>
                  )}

                  {/* attack debt with genuine spare — record the prepayment */}
                  {move.kind === 'attack_debt' && move.target && mayEdit && (
                    <button
                      onClick={() => setPayFor({ id: move.target!.id, name: move.target!.name, amount: Number(move.facts.actionable) || 0 })}
                      className="mt-3 inline-flex min-h-11 items-center gap-1.5 rounded-full bg-accent px-4 text-xs font-bold text-white hover:bg-accent-2 active:scale-95"
                    >
                      Record this payment →
                    </button>
                  )}

                  {move.kind === 'unknown' && (
                    <Link to="/plan" className="mt-2 inline-block text-xs font-semibold text-accent">Set up Plan ›</Link>
                  )}
                </div>
              )}

              {/* payoff plan — a phased TIMELINE from today. Each loan gets a month-range + a real date,
                  and the rolldown is spelled out so nothing is a mystery "+10mo". */}
              {sel && sel.steps.length > 0 && (
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-[0.09em] text-ink-3">
                    Your plan, in the order they finish · {sel.label}
                    {sel.key === recommendedKey && <span className="ml-1.5 normal-case tracking-normal text-accent">recommended</span>}
                  </h3>
                  <div className="mb-1 mt-1 flex flex-wrap items-baseline gap-x-2">
                    <span className="font-display text-lg font-bold text-ink">
                      {sel.complete ? `All loans paid off by ${clearDate(sel.months)}` : `Not all loans clear within ${fmtDur(sel.months)}`}
                    </span>
                    {sel.complete && <span className="text-xs text-ink-3">that’s {fmtDur(sel.months)} from now</span>}
                  </div>
                  {/* The date assumes the extra goes in every month. When it has not been, show the
                      date that follows from what is actually happening, beside it. */}
                  {(() => {
                    const alt = strategies?.at_actual?.[sel.key]
                    const adh = strategies?.adherence
                    if (!alt || !adh?.actual_avg) return null
                    return (
                      <p className="mb-2 text-[11px] leading-relaxed text-negative">
                        At what you’ve actually been putting in ({shortINR(adh.actual_avg)}/mo over{' '}
                        {adh.months.length} months, plan assumes {shortINR(adh.planned_per_month)}):{' '}
                        <span className="font-semibold">
                          {alt.complete ? clearDate(alt.months) : `not cleared in ${fmtDur(alt.months)}`}
                        </span>
                        {alt.complete && <>, {fmtDur(alt.months - sel.months)} later</>}
                      </p>
                    )
                  })()}
                  {/* When every ordering finishes in the same month, leading with the date makes the
                      picker look broken: you switch strategy and nothing moves. The date is fixed by
                      total debt and total outflow; only the interest and the order actually change. */}
                  {(() => {
                    const sameDate = options.length > 1 && new Set(options.map((o) => o.months)).size === 1
                    if (!sameDate) return null
                    const cheapest = options.reduce((a, b) => (a.total_interest <= b.total_interest ? a : b))
                    const dearest = options.reduce((a, b) => (a.total_interest >= b.total_interest ? a : b))
                    const spread = dearest.total_interest - cheapest.total_interest
                    return (
                      <p className="mb-2 text-[11px] leading-relaxed text-ink-3">
                        Same finish date every method · interest differs by{' '}
                        <span className="money text-ink-2">{shortINR(spread)}</span>, {cheapest.label.toLowerCase()} cheapest
                      </p>
                    )
                  })()}
                  {recommended && sel.key !== recommended.key && (
                    <p className="mb-1 text-xs leading-relaxed text-ink-2">
                      You’re looking at the {sel.label.toLowerCase()} plan. We’d recommend{' '}
                      <button onClick={() => pickStrategy(recommended.key)} className="font-semibold text-accent hover:underline">
                        {recommended.label.toLowerCase()}
                      </button>{' '}instead.
                    </p>
                  )}

                  {/* Explain the mechanic ONCE, in three plain steps, instead of re-explaining it inside
                      every row. This is what made the old copy unreadable. */}
                  <div className="mb-3 mt-2 rounded-2xl border border-line bg-surface-2 p-3">
                    <div className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-3">How this works</div>
                    <ol className="mt-1.5 space-y-1 text-xs leading-relaxed text-ink-2">
                      <li>1. Keep paying every loan’s normal EMI, as usual.</li>
                      <li>2. Put your spare {shortINR(strategies?.plan_extra ?? 0)}/mo on <span className="font-semibold text-ink">one</span> loan at a time, in the order below.</li>
                      <li>3. When a loan is done, the EMI you were paying on it moves to the next loan. That’s why the monthly amount grows and each loan clears faster than the last.</li>
                    </ol>
                  </div>

                  <Card padded={false} className="px-4">
                    {sel.steps.map((s, i) => {
                      const prev = i > 0 ? sel.steps[i - 1].closes_in_months : 0
                      const startM = (prev ?? 0) + 1
                      const span = s.closes_in_months != null ? s.closes_in_months - (prev ?? 0) : null
                      return (
                        <div key={s.id} className="flex gap-3 border-b border-line py-3 last:border-0">
                          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-bold text-accent">{i + 1}</span>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-baseline justify-between gap-2">
                              <span className="truncate text-sm font-semibold text-ink">{s.name}</span>
                              <span className="shrink-0 text-xs font-semibold text-ink">
                                {s.closes_in_months != null ? `done ~${clearDate(s.closes_in_months)}` : 'not cleared'}
                                {(() => {
                                  const alt = strategies?.at_actual?.[sel.key]?.steps?.find((x) => x.id === s.id)
                                  if (!alt || alt.closes_in_months === s.closes_in_months) return null
                                  return (
                                    <span className="ml-1 text-negative">
                                      ({alt.closes_in_months != null ? clearDate(alt.closes_in_months) : 'not cleared'} at your rate)
                                    </span>
                                  )
                                })()}
                              </span>
                            </div>
                            {s.closes_in_months != null ? (
                              <>
                                <div className="mt-0.5 text-xs leading-relaxed text-ink">
                                  Pay <span className="money font-semibold">{shortINR(s.monthly_payment)}</span>/mo
                                  {span != null && <> for {span === 1 ? '1 month' : `${span} months`}</>}
                                  <span className="text-ink-3"> (month {startM}–{s.closes_in_months})</span>
                                </div>
                                {/* the split is the WHY: a loan closes fast because the pool lands on it */}
                                <div className="mt-0.5 text-[11px] leading-relaxed text-ink-3">
                                  <span className="money">{shortINR(s.own_emi)}</span> its own EMI
                                  {s.from_pool > 0 ? (
                                    <> + <span className="money">{shortINR(s.from_pool)}</span> rolled down from your spare cash
                                      {i > 0 && <> and the loans already paid off</>} ={' '}
                                      <span className="money font-semibold text-ink-2">{shortINR(s.monthly_payment)}</span>/mo</>
                                  ) : (
                                    <> — nothing rolled onto it yet</>
                                  )}
                                </div>
                                {/* A big later figure reads as money you do not have. Name the month it
                                    starts and whose EMIs pay for it, so it stops looking impossible. */}
                                {(s.funded_by_closing?.length ?? 0) > 0 && (
                                  <div className="mt-0.5 text-[11px] leading-relaxed text-ink-3">
                                    from {clearDate((s.peak_from_month ?? 1) - 1)}, once{' '}
                                    {s.funded_by_closing!.slice(0, 2).join(' and ')}
                                    {s.funded_by_closing!.length > 2 && ` +${s.funded_by_closing!.length - 2}`} free up
                                  </div>
                                )}
                              </>
                            ) : (
                              <div className="mt-0.5 text-xs leading-relaxed text-ink-3">
                                Still open at the end of the plan. Its payment of {shortINR(s.own_emi)}/mo doesn’t clear it in this window.
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </Card>
                  <SavingsVsEmis o={sel} className="mt-3 rounded-2xl border border-line bg-surface-2 p-3" />
                  {(sel.never_amortises?.length ?? 0) > 0 && (
                    <p className="mt-2 text-[11px] leading-relaxed text-warn">
                      Heads up: {sel.never_amortises!.join(', ')} — the amount you pay only covers the interest, so the
                      balance doesn’t come down on its own. It clears only because the rolled-down pool reaches it.
                    </p>
                  )}
                </div>
              )}
            </section>
          )}

          {/* The argument BEFORE the detail: the staircase says why this order clears faster, the
              loan cards below say what each loan is. It was seven numbered cards of prose making a
              point about acceleration, which is a shape, not a sentence. */}
          {sel && sel.steps.length > 1 && (
            <PayoffWaterfall steps={sel.steps} spare={strategies?.extra_per_month ?? 0} className="mb-5" />
          )}

          {/* active loans, ordered by the selected strategy */}
          <section className="space-y-5">
            <SectionHeader
              level={2}
              title={sel ? `${sel.label} order` : 'Your loans'}
              sub={sel?.description}
              action={
                mayEdit && !adding && (
                  <button
                    onClick={() => setAdding(true)}
                    className="min-h-11 rounded-input border border-line px-3 text-xs font-semibold text-ink-2 hover:border-accent hover:text-accent"
                  >
                    + Add loan
                  </button>
                )
              }
            />

            {adding && <AddLoanForm onClose={() => setAdding(false)} />}

            {ordered.length > 0 ? (
              <PageGrid cols="auto" min="20rem" gap="gap-4">
                {ordered.map((loan) => (
                  <LoanCard key={loan.id} loan={loan} isTarget={loan.id === attackId} />
                ))}
              </PageGrid>
            ) : (
              <div className="rounded-2xl border border-dashed border-line bg-surface-2 px-4 py-8 text-center text-sm text-ink-3">
                No active loans. {closedLoans.length > 0 ? 'All paid off.' : 'Add one above to start a payoff plan.'}
              </div>
            )}
          </section>

          {/* external loans — pledge gold but not your cash-flow (kept out of FOIR + the payoff plan) */}
          {externalLoans.length > 0 && (
            <section className="space-y-5">
              <SectionHeader level={2} title={`External · ${externalLoans.length}`}
                sub="Someone else's EMI (e.g. parents' gold loan) — pledges gold, excluded from your FOIR & plan" />
              <PageGrid cols="auto" min="20rem" gap="gap-4">
                {externalLoans.map((loan: any) => (
                  <LoanCard key={loan.id} loan={loan} isTarget={false} />
                ))}
              </PageGrid>
            </section>
          )}

          {/* closed loans — a clear record that a debt is done */}
          {closedLoans.length > 0 && (
            <section className="space-y-5">
              <SectionHeader level={2} title={`Closed · ${closedLoans.length}`} sub="Paid off & foreclosed loans" />
              <PageGrid cols="auto" min="20rem" gap="gap-4">
                {closedLoans.map((loan: any) => (
                  <LoanCard key={loan.id} loan={loan} isTarget={false} />
                ))}
              </PageGrid>
            </section>
          )}
        </>
      )}

      {payFor && <RecordPaymentSheet key={payFor.id} pay={payFor} onClose={() => setPayFor(null)} />}
    </PageContainer>
  )
}
