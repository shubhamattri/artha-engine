import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  clearMeasuredReturn,
  fetchWindfall,
  formatINR,
  currencySymbol,
  saveMeasuredReturn,
  type WindfallResponse,
  type WindfallScenario,
} from '../api/client'
import {
  Card,
  EmptyState,
  ErrorState,
  Field,
  Icon,
  Loading,
  PageContainer,
  PageGrid,
  SectionHeader,
  inputClass,
  Provenance,
} from '../components/ui'
import { ViewOnly, useCan } from '../auth/Can'

/**
 * Windfall — one lump sum, shown as BOTH "prepay the debt" and "invest it".
 *
 * The page deliberately does NOT pick. There is no recommended badge, no winner styling, no ranking
 * and no copy telling anyone what to do. What it does instead is make the two figures look like the
 * different KINDS of number they are, because that is the thing a side-by-side comparison destroys:
 *
 *   certain   → solid card, solid accent rail, a plain figure
 *   uncertain → tinted surface, DASHED border and rail, a "≈" and a RANGE, never one number
 *
 * Every rate carries its assumption next to it. A bare "12%" on a screen is how this app once
 * reported a portfolio 41% above reality, so no rate is ever rendered on its own here.
 *
 * Two things the page must keep saying out loud, because both were once invisible:
 *   the HORIZON is a choice, not an artefact of whichever loan runs longest;
 *   the SURPLUS is already going at the debt in both arms — the windfall is the only thing decided.
 */

function fmtMonths(m: number | null): string {
  if (m === null) return 'not within the plan'
  if (m <= 0) return '0 mo'
  const y = Math.floor(m / 12)
  const r = m % 12
  if (!y) return `${r} mo`
  return r ? `${y}y ${r}m` : `${y} yr${y > 1 ? 's' : ''}`
}

/** The one badge that carries the whole distinction. Solid = contracted, dashed = assumed. */
function CertaintyChip({ kind }: { kind: 'certain' | 'uncertain' }) {
  const certain = kind === 'certain'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-chip px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider ${
        certain
          ? 'bg-accent-soft text-accent'
          : 'border border-dashed border-line-2 bg-surface text-ink-2'
      }`}
    >
      <Icon name={certain ? 'check' : 'info'} size={13} />
      {certain ? 'Certain' : 'Uncertain'}
    </span>
  )
}

function Panel({
  kind, title, chipRow, children,
}: {
  kind: 'certain' | 'uncertain'
  title: string
  chipRow?: React.ReactNode
  children: React.ReactNode
}) {
  const certain = kind === 'certain'
  return (
    <section
      className={`rounded-2xl border p-4 sm:p-5 ${
        certain
          ? 'border-line border-l-[3px] border-l-accent bg-surface'
          : 'border-dashed border-line-2 border-l-[3px] border-l-line-2 bg-surface-2'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-bold leading-tight text-ink">{title}</h2>
        <CertaintyChip kind={kind} />
      </div>
      {chipRow}
      {children}
    </section>
  )
}

/** A figure and what it is. `approx` prefixes ≈ — the at-a-glance tell that it is not contracted. */
function Figure({
  label, value, hint, approx = false,
}: { label: string; value: string; hint?: string; approx?: boolean }) {
  return (
    <div>
      <div className="tracked text-[10px] font-semibold text-ink-3">{label}</div>
      <div className={`money mt-1 text-2xl font-bold leading-tight text-ink ${approx ? 'opacity-90' : ''}`}>
        {approx && <span className="mr-1 font-normal text-ink-3">≈</span>}
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-ink-2">{hint}</div>}
    </div>
  )
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="tracked text-[10px] font-semibold text-ink-3">{label}</div>
      <div className="money mt-1 text-base font-bold text-ink">{value}</div>
    </div>
  )
}

/** Four claims that must never render alike. The badge itself is `ui/Provenance`, shared, because
 *  this is the app's honesty grammar and it belongs in one place rather than inside this page. */
function SourceTag({ s }: { s: WindfallScenario }) {
  return (
    <Provenance
      source={s.source}
      measuredBy={s.observed_from}
      asOf={s.as_of}
      stale={s.stale}
    />
  )
}

function ScenarioRow({ s, amount }: { s: WindfallScenario; amount: number }) {
  return (
    <li className="border-t border-line py-3 first:border-t-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {/* A rate is NEVER shown bare — the tag next to it says where it came from. */}
          <span className="money text-sm font-bold text-ink">{s.rate_pct.toFixed(1)}% a year</span>
          <SourceTag s={s} />
        </div>
        <div className="money text-sm font-semibold text-ink-2">
          <span className="mr-1 font-normal text-ink-3">≈</span>
          {formatINR(s.value_inr)}
          {amount > 0 && <span className="ml-2 text-ink-3">+{formatINR(s.gain_inr)}</span>}
        </div>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-ink-3">{s.assumption}</p>
    </li>
  )
}

function PrepayPanel({ data }: { data: WindfallResponse }) {
  const p = data.prepay
  return (
    <Panel kind="certain" title="If it goes at the debt">
      <p className="mt-2 text-sm text-ink-2">{p.basis}.</p>

      {p.considered.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            icon="card"
            title="No debt this money could go at"
            hint="Loans somebody else pays, and loans whose principal is somebody else's, are left out on purpose."
          />
        </div>
      ) : (
        <>
          <div className="mt-4">
            <Figure
              label={`Interest you would not pay, over ${fmtMonths(p.horizon_months)}`}
              value={formatINR(p.interest_avoided_inr)}
              hint={`On top of what the surplus already avoids, ${p.order}. Over the whole remaining
                     plan it is ${formatINR(p.interest_avoided_full_term_inr)}.`}
            />
          </div>

          <div className="mt-4 grid grid-cols-2 gap-4">
            <Cell label="EMI freed each month" value={formatINR(p.emi_freed_per_month_inr)} />
            <Cell label="Debt-free sooner by" value={fmtMonths(p.months_removed)} />
          </div>

          {/* The comparison this figure is against. It used to be a world with no surplus in it,
              which credited the windfall with interest the surplus was going to avoid anyway. */}
          <div className="mt-4 rounded-xl border border-line bg-surface-2 p-3">
            <div className="tracked text-[10px] font-semibold text-ink-3">Measured against</div>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              {p.baseline.what} —{' '}
              <span className="money font-semibold text-ink">
                {formatINR(p.surplus.assumed_per_month_inr)}/mo
              </span>
              , debt-free in {fmtMonths(p.baseline.months)}. With the windfall in as well:{' '}
              {fmtMonths(p.with_windfall.months)}.
            </p>
            <p className="mt-2 text-xs leading-relaxed text-ink-3">
              {p.surplus.why_this_one}. {p.surplus.source}.
              {p.surplus.actionable_inr != null &&
                p.surplus.surplus_inr != null &&
                p.surplus.actionable_inr < p.surplus.surplus_inr && (
                  <>
                    {' '}Safe to move this cycle is lower, at{' '}
                    <span className="money">{formatINR(p.surplus.actionable_inr)}</span>, because
                    this cycle is over budget.
                  </>
                )}
            </p>
          </div>

          {p.loans.length > 0 && (
            <ul className="mt-4">
              {p.loans.map((l) => (
                <li key={l.id} className="border-t border-line py-3 first:border-t-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <span className="text-sm font-semibold text-ink">{l.name}</span>
                    <span className="money text-sm text-ink-2">{formatINR(l.applied_inr)}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-2">
                    <span className="money">{(l.rate_pct ?? 0).toFixed(2)}% contracted</span>
                    <span className="text-ink-3">·</span>
                    <span>
                      {l.cleared
                        ? `clears it today, freeing ${formatINR(l.emi_freed_inr)}/mo`
                        : `${fmtMonths(l.months_before)} → ${fmtMonths(l.months_after)}`}
                    </span>
                    {l.interest_avoided_inr > 0 && (
                      <>
                        <span className="text-ink-3">·</span>
                        <span className="money text-accent">
                          {formatINR(l.interest_avoided_inr)} interest avoided
                        </span>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {p.amount_unused_inr > 0 && (
            <p className="mt-3 text-xs text-ink-2">
              <span className="money">{formatINR(p.amount_unused_inr)}</span> has nowhere to go —
              every loan that will take early money is already cleared.
            </p>
          )}

          {p.interest_incomplete.length > 0 && (
            <p className="mt-3 rounded-xl bg-surface-2 p-3 text-xs text-warn">
              {p.interest_incomplete.join(', ')} pays less than its own monthly interest, so its
              remaining interest cannot be known. The figure above is a floor, not the whole picture.
            </p>
          )}

          {p.excluded.length > 0 && (
            <details className="mt-3">
              <summary className="flex min-h-11 cursor-pointer items-center text-xs font-semibold text-ink-2">
                {p.excluded.length} loan{p.excluded.length > 1 ? 's' : ''} left out, and why
              </summary>
              <ul className="mt-1 space-y-2">
                {p.excluded.map((e) => (
                  <li key={e.id} className="text-xs text-ink-3">
                    <span className="font-semibold text-ink-2">{e.name}</span> — {e.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </Panel>
  )
}

function InvestPanel({ data }: { data: WindfallResponse }) {
  const i = data.invest
  const r = i.range
  const m = i.measured_return
  return (
    <Panel kind="uncertain" title="If it stays invested">
      <p className="mt-2 text-sm text-ink-2">{i.basis}.</p>

      <div className="mt-4">
        <Figure
          approx
          label={`What it could be worth in ${fmtMonths(i.horizon_months)}`}
          value={`${formatINR(r.low_value_inr)} – ${formatINR(r.high_value_inr)}`}
          hint={`Across ${r.low_rate_pct.toFixed(1)}%–${r.high_rate_pct.toFixed(1)}% a year. ${i.method}`}
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Cell
          label="Gain, low assumption"
          value={`≈ ${formatINR(r.low_gain_inr)}`}
        />
        <Cell
          label="Gain, high assumption"
          value={`≈ ${formatINR(r.high_gain_inr)}`}
        />
      </div>

      {/* Both arms have the surplus flowing. Investing the windfall does not switch the debt off. */}
      <p className="mt-4 rounded-xl border border-line bg-surface p-3 text-xs leading-relaxed text-ink-2">
        {i.while_invested.note} — <span className="money font-semibold text-ink">
          {formatINR(i.while_invested.surplus_per_month_inr)}/mo
        </span>{' '}
        keeps running, debt-free in {fmtMonths(i.while_invested.debt_months)} either way.
      </p>

      <div className="tracked mt-5 text-[10px] font-semibold text-ink-3">Every rate, and what it assumes</div>
      <ul className="mt-1">
        {i.scenarios.map((s) => (
          <ScenarioRow key={s.key} s={s} amount={data.amount_inr} />
        ))}
      </ul>

      {i.realised_xirr_unavailable_why && (
        <p className="mt-3 rounded-xl border border-dashed border-line-2 p-3 text-xs text-ink-2">
          {i.realised_xirr_unavailable_why}
        </p>
      )}

      {m?.superseded_by_derived && (
        <p className="mt-3 rounded-xl border border-dashed border-line-2 p-3 text-xs text-ink-2">
          The {m.rate_pct.toFixed(1)}% you entered by hand ({m.scope}, as of {m.as_of}) is kept but
          is no longer the anchor: Artha can now measure this itself from your own holdings, and a
          figure it can re-check does not go stale in silence.
        </p>
      )}

      {m?.stale && m.in_use && (
        <p className="mt-3 rounded-xl border border-line-2 bg-warn/5 p-3 text-xs text-warn">
          That measured return is {m.as_of_days_ago} days old, past the {m.stale_after_days}-day
          mark. Read it again from the source and re-enter it.
        </p>
      )}
    </Panel>
  )
}

/** The window both sides are worked out over. A choice, shown as one — not a number the data emits. */
function HorizonPicker({
  data, value, onChange,
}: {
  data: WindfallResponse
  value: number | null
  onChange: (m: number | null) => void
}) {
  return (
    <div>
      <div className="tracked text-[10px] font-semibold text-ink-3">Worked out over</div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {data.horizon_choices.map((c) => {
          const on = data.horizon_months === c.months
          return (
            <button
              key={c.months}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(c.months)}
              className={`inline-flex min-h-11 items-center rounded-xl border px-3 text-xs font-semibold ${
                on ? 'border-accent bg-accent-soft text-accent' : 'border-line text-ink-2 hover:border-line-2'
              }`}
            >
              {c.label}
            </button>
          )
        })}
        {value !== null && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="inline-flex min-h-11 items-center rounded-xl border border-line px-3 text-xs font-semibold text-ink-2 hover:border-line-2"
          >
            Use the default
          </button>
        )}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-ink-2">
        Both sides are worked out over the same{' '}
        <span className="font-semibold text-ink">{fmtMonths(data.horizon_months)}</span> —{' '}
        {data.horizon_basis}.
      </p>
    </div>
  )
}

/**
 * The household's own measured return, when Artha cannot derive one. It is typed in, so it carries
 * the date it measures to and what it refers to — a rate with neither ages into a lie in silence,
 * and that silent decay is the exact failure this whole page exists to end.
 */
function MeasuredReturnForm({ data }: { data: WindfallResponse }) {
  const qc = useQueryClient()
  const mayEdit = useCan('windfall', 'edit')
  const m = data.invest.measured_return
  const [open, setOpen] = useState(false)
  const [rate, setRate] = useState('')
  const [scope, setScope] = useState('')
  const [asOf, setAsOf] = useState('')
  const [error, setError] = useState<string | null>(null)

  const done = () => {
    qc.invalidateQueries({ queryKey: ['windfall'] })
    setOpen(false)
    setError(null)
  }
  const fail = (e: unknown) =>
    setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      ?? 'Could not save that.')

  const save = useMutation({ mutationFn: saveMeasuredReturn, onSuccess: done, onError: fail })
  const remove = useMutation({ mutationFn: clearMeasuredReturn, onSuccess: done, onError: fail })

  const start = () => {
    setRate(m ? String(m.rate_pct) : '')
    setScope(m?.scope ?? '')
    setAsOf(m?.as_of ?? new Date().toISOString().slice(0, 10))
    setError(null)
    setOpen(true)
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-ink">A return measured somewhere else</h2>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-ink-2">
            {m ? (
              <>
                <span className="money font-semibold text-ink">{m.rate_pct.toFixed(2)}% a year</span>
                {' '}— {m.scope}, as of {m.as_of}, entered by hand
                {m.as_of_days_ago != null && ` (${m.as_of_days_ago} days ago)`}.{' '}
                {m.in_use
                  ? 'Used as the measured anchor below.'
                  : 'Kept, but not the anchor: Artha can derive one itself now.'}
              </>
            ) : (
              'If your broker reports a realised XIRR that Artha cannot work out from what it holds, '
              + 'record it here. It is a measurement, not an assumption, and it will be labelled as '
              + 'measured elsewhere rather than folded in with the assumed rates.'
            )}
          </p>
        </div>
        {mayEdit && !open && (
          <div className="flex flex-wrap gap-2">
            <button
              onClick={start}
              className="inline-flex min-h-11 items-center rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:border-line-2"
            >
              {m ? 'Update it' : 'Record one'}
            </button>
            {m && (
              <button
                onClick={() => remove.mutate()}
                className="inline-flex min-h-11 items-center rounded-xl border border-line px-4 text-sm font-semibold text-ink-2 hover:border-line-2"
              >
                Remove
              </button>
            )}
          </div>
        )}
      </div>

      {/* The form below only opens from those buttons, so it is unreachable without them. */}
      {!mayEdit && <div className="mt-3"><ViewOnly what="the return recorded here" /></div>}

      {open && (
        <form
          className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate({ rate_pct: Number(rate), scope: scope.trim(), as_of: asOf })
          }}
        >
          <Field label="Realised return, % a year" hint="As your broker reports it.">
            <input
              className={`${inputClass} money`}
              inputMode="decimal"
              aria-label="Measured return percent"
              placeholder="10.43"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
            />
          </Field>
          <Field label="What it refers to" hint="So a reader knows what was measured.">
            <input
              className={inputClass}
              aria-label="What the measured return refers to"
              placeholder="every equity holding at the broker"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            />
          </Field>
          <Field label="Measured as of" hint="The date the figure is true to.">
            <input
              className={inputClass}
              type="date"
              aria-label="Date the measured return is as of"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
            <button
              type="submit"
              disabled={save.isPending}
              className="inline-flex min-h-11 items-center rounded-xl bg-accent px-4 text-sm font-semibold text-white disabled:opacity-50"
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="inline-flex min-h-11 items-center rounded-xl border border-line px-4 text-sm font-semibold text-ink-2 hover:border-line-2"
            >
              Cancel
            </button>
            {error && <span className="text-xs text-negative">{error}</span>}
          </div>
        </form>
      )}
    </Card>
  )
}

export default function Windfall() {
  // `null` = "whatever the household's investable cash is". The box is only bound to a number once
  // a person types one, so the default can move with the data instead of freezing at first render.
  const [typed, setTyped] = useState<string | null>(null)
  const [applied, setApplied] = useState<number | null>(null)
  // `null` = "the app's stated default". Kept null rather than seeded with a number, so the default
  // lives in exactly one place — the API — and this screen can never disagree with it.
  const [horizon, setHorizon] = useState<number | null>(null)

  useEffect(() => {
    if (typed === null) {
      setApplied(null)
      return
    }
    const t = setTimeout(() => {
      const n = Number(typed.replace(/[^0-9.]/g, ''))
      setApplied(Number.isFinite(n) && n >= 0 ? n : 0)
    }, 300)
    return () => clearTimeout(t)
  }, [typed])

  const q = useQuery({
    queryKey: ['windfall', applied, horizon],
    queryFn: () => fetchWindfall(applied, horizon),
    placeholderData: (prev) => prev,
  })

  const data = q.data
  const cash = data?.investable_cash

  return (
    <PageContainer>
      <SectionHeader
        title="Windfall"
        sub="One lump sum, shown both ways. This page works out each path and stops there."
      />

      <Card>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,20rem)_1fr] md:items-start">
          <Field
            label="Amount"
            hint={
              cash?.data_ok
                ? `Defaults to your investable cash, ${formatINR(cash.amount_inr)} — ${cash.basis}.`
                : 'Your investable cash could not be read, so this starts at zero.'
            }
          >
            <div className="relative">
              <span className="money pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-ink-3">
                {currencySymbol()}
              </span>
              <input
                className={`${inputClass} money pl-8`}
                inputMode="decimal"
                aria-label="Windfall amount"
                placeholder={cash ? String(Math.round(cash.amount_inr)) : '0'}
                value={typed ?? (data ? String(Math.round(data.amount_inr)) : '')}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
          </Field>

          <div className="flex flex-col gap-3">
            {typed !== null && (
              <button
                onClick={() => setTyped(null)}
                className="inline-flex min-h-11 w-fit items-center rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:border-line-2"
              >
                Use my investable cash
              </button>
            )}
            {data && <HorizonPicker data={data} value={horizon} onChange={setHorizon} />}
          </div>
        </div>
      </Card>

      {data && <MeasuredReturnForm data={data} />}

      {q.isError && <ErrorState message={(q.error as Error)?.message} onRetry={() => q.refetch()} />}

      {!data && q.isLoading && <Loading rows={4} label="Working out both paths" />}

      {data && data.amount_inr <= 0 && data.prepay.considered.length === 0 && (
        <EmptyState
          icon="wallet"
          title="Nothing to compare yet"
          hint="Enter an amount above, or add a loan and some cash so this can read them."
        />
      )}

      {data && (data.amount_inr > 0 || data.prepay.considered.length > 0) && (
        <>
          <PageGrid cols={2} gap="gap-5">
            <PrepayPanel data={data} />
            <InvestPanel data={data} />
          </PageGrid>

          <Card className="border-dashed">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 shrink-0 text-ink-3">
                <Icon name="info" size={18} />
              </span>
              <div className="space-y-2 text-sm text-ink-2">
                <p>{data.certainty.comparable_why}.</p>
                <p>
                  <span className="font-semibold text-ink">Left:</span>{' '}
                  {data.certainty.prepay.why}.{' '}
                  <span className="font-semibold text-ink">Right:</span>{' '}
                  {data.certainty.invest.why}.
                </p>
              </div>
            </div>
          </Card>
        </>
      )}

      {data && <p className="text-xs leading-relaxed text-ink-3">{data.disclaimer}</p>}
    </PageContainer>
  )
}
