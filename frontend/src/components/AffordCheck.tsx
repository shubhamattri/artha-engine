/**
 * "Can I afford it?" — the deterministic check, on the screen the app already points at.
 *
 * The Log-a-spend sheet tells the reader that affordability "lives in Ask Artha", and Ask Artha only
 * ever called /advisor — so the promise led nowhere. This restores the endpoint behind it.
 *
 * It sits ABOVE the chat and looks unlike it on purpose. /afford returns a computed verdict off the
 * binding constraint (the tighter of the overall free budget and the chosen category, or FOIR + LTV
 * for a capital purchase); only the closing sentence is narrated. So the figures get a solid card and
 * the sentence gets marked, rather than the whole answer being dressed as a reading.
 */
import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { checkAfford, type AffordResult } from '../api/afford'
import { currencySymbol, fetchCategories, formatINR } from '../api/client'
import { useCan } from '../auth/Can'
import { Card, Icon, SelectInput, TextInput } from './ui'

const VERDICT: Record<AffordResult['verdict'], { label: string; colour: string }> = {
  go: { label: 'Go ahead', colour: 'var(--accent)' },
  tight: { label: 'Tight', colour: 'var(--warn)' },
  hold: { label: 'Hold', colour: 'var(--negative)' },
}

function Row({ label, value, tone = 'text-ink' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-[13px]">
      <span className="text-ink-2">{label}</span>
      <span className={`money font-medium ${tone}`}>{value}</span>
    </div>
  )
}

function Everyday({ r }: { r: AffordResult }) {
  const left = r.remaining_after ?? 0
  return (
    <>
      <Row label="Free this cycle" value={formatINR(r.monthly_free ?? 0)} />
      <Row label="Already spent" value={`−${formatINR(r.spent_so_far ?? 0)}`} tone="text-ink-2" />
      <Row label="Left after this" value={formatINR(left)}
           tone={left < 0 ? 'text-negative' : 'text-ink'} />
      {r.binds_on_category && (
        <p className="pt-1 text-[11.5px] leading-relaxed text-ink-3">
          The category budget is the tighter of the two limits, so it is the one deciding this.
        </p>
      )}
    </>
  )
}

function BigTicket({ r }: { r: AffordResult }) {
  return (
    <>
      <Row label="Eligible loan" value={formatINR(r.max_loan ?? 0)} />
      <Row label="Down payment needed" value={formatINR(r.down_payment ?? 0)} />
      {!!r.funding?.length && (
        <p className="py-1 text-[11.5px] leading-relaxed text-ink-3">
          From {r.funding.map((f) => `${f.source} ${formatINR(f.amount)}`).join(' · ')}
          {r.emergency_buffer ? `, keeping ${formatINR(r.emergency_buffer)} as an emergency buffer` : ''}.
        </p>
      )}
      {!!r.shortfall && <Row label="Short by" value={formatINR(r.shortfall)} tone="text-negative" />}
      <Row label="New EMI" value={formatINR(r.new_emi ?? 0)} />
      <Row label="All EMIs vs income" value={`${r.foir_pct ?? 0}%`}
           tone={r.emi_ok ? 'text-ink' : 'text-negative'} />
    </>
  )
}

export default function AffordCheck() {
  // Read-only POST: permissions.py declares ("POST", "/api/afford") as spending:view and lists it in
  // READ_ONLY_POSTS, so someone who may look but not edit can still ask.
  const mayAsk = useCan('spending')
  const [open, setOpen] = useState(false)
  const [amount, setAmount] = useState('')
  const [category, setCategory] = useState('')
  const cats = useQuery({ queryKey: ['categories'], queryFn: fetchCategories, enabled: open })
  const check = useMutation({
    mutationFn: () => checkAfford(parseFloat(amount), category || undefined),
  })

  if (!mayAsk) return null
  const amt = parseFloat(amount)
  const valid = !Number.isNaN(amt) && amt > 0
  const options = (cats.data ?? []).filter((c) => c.is_spend).map((c) => c.value)

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        className="mb-3 flex min-h-11 w-full items-center justify-between rounded-2xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:border-line-2">
        Can I afford it?
        <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-3">
          a computed answer <Icon name="chevron" size={15} />
        </span>
      </button>
    )
  }

  return (
    <div className="mb-3">
      <Card>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-ink">Can I afford it?</p>
            <p className="mt-0.5 text-[11.5px] text-ink-3">Checked against your free budget, not guessed.</p>
          </div>
          <button onClick={() => { setOpen(false); check.reset() }} aria-label="Close"
            className="flex min-h-11 min-w-11 items-center justify-center text-ink-3 hover:text-ink">
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <TextInput label="Amount" placeholder={`${currencySymbol()}0`} inputMode="decimal"
            value={amount} onChange={(e) => setAmount(e.target.value)} />
          <SelectInput label="Category (optional)" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Any</option>
            {options.map((c) => <option key={c} value={c}>{c}</option>)}
          </SelectInput>
        </div>

        <button onClick={() => check.mutate()} disabled={!valid || check.isPending}
          className="mt-3 min-h-11 w-full rounded-input bg-accent text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50">
          {check.isPending ? 'Checking…' : 'Check'}
        </button>

        {check.isError && (
          <p className="mt-3 text-[12.5px] text-negative">Couldn&rsquo;t run the check just now. Try again.</p>
        )}
      </Card>

      {check.data && <Verdict r={check.data} />}
    </div>
  )
}

function Verdict({ r }: { r: AffordResult }) {
  const v = VERDICT[r.verdict]
  return (
    <Card className="mt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 text-[10px] font-semibold uppercase"
              style={{ letterSpacing: '.1em', color: v.colour }}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: v.colour }} />
          {v.label}
        </span>
        <span className="money text-sm font-semibold text-ink">{formatINR(r.amount)}</span>
      </div>

      <div className="mt-3 border-t border-line pt-2">
        {r.big_ticket ? <BigTicket r={r} /> : <Everyday r={r} />}
      </div>

      <p className="mt-3 border-t border-line pt-2.5 text-[13px] leading-relaxed text-ink">{r.reason}</p>
      {r.ai && (
        <span className="tracked mt-1.5 block text-[10px] text-ink-3">
          ✦ AI wording — the figures above are computed
        </span>
      )}
    </Card>
  )
}
