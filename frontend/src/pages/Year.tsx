/**
 * The year — one screen, one question: does the annual envelope hold?
 *
 * Every other page in this app stacks loosely related sections, which is why there was never an
 * obvious place to put anything new. This one does a single job and stops.
 *
 * The design decision worth knowing about is what is NOT here. The first version split spend into
 * "everyday" and "one-off" and compared only the everyday half to the target, which reads well and
 * was measured to be meaningless: moving the size threshold from 2% to 40% of a monthly target
 * swung everyday spend several-fold and flipped the verdict from "on plan" to
 * "43% over". A screen whose headline depends on a constant the reader cannot see is not reporting,
 * it is asserting.
 *
 * So the household draws the line instead. The biggest purchases are listed BY NAME, and excluding
 * one recomputes what the rest of the year costs per month. Recognising your own car dealer in a
 * list is something a person does instantly and no statistic conveys.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import {
  fetchYear, formatINR, merchantLabel, shortINR, type YearMonth, type YearResponse,
} from '../api/client'
import {
  Card, ErrorState, Icon, Loading, PageContainer, PeriodToggle, SectionHeader,
} from '../components/ui'
import { usePeriod } from '../hooks/usePeriod'

/** Bars are drawn against TWICE the monthly target, so the target line sits at exactly half the
 *  track and stays readable. Scaling to the tallest bar instead would put an 11-lakh month at the
 *  top and squash the target line to a tenth of the height, which hides the only reference the
 *  chart has. Anything past the ceiling is cut off visibly and prints its real figure. */
const TRACK_H = 132
const CEILING = 2

function monthLetter(month: string): string {
  return new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1, 1)
    .toLocaleString('en-IN', { month: 'short' })
}

function dayMon(iso: string): string {
  const d = new Date(iso)
  return `${d.getDate()} ${d.toLocaleString('en-IN', { month: 'short' })}`
}

/**
 * One bar, in two parts: what fitted inside that month's target, and what went over it.
 *
 * The split is min and max against the month's own target, so the green segment always ends exactly
 * at the target line and everything red is, literally, the overshoot. A single-colour bar could only
 * say "this month was over"; this says by how much, in the same glance, and needs no threshold to
 * do it.
 *
 * A bar that has been cut off must LOOK cut off, or the chart quietly lies about the tallest months.
 * The notch is the signal; the printed figure is the recovery.
 */
function Bar({ m }: { m: YearMonth }) {
  const target = m.target_inr || 0
  const spent = m.net_spent_inr
  const scale = target ? target * CEILING : spent || 1
  const within = m.within_target_inr ?? (spent === null ? 0 : Math.min(spent, target))
  const above = m.above_target_inr ?? (spent === null ? 0 : Math.max(0, spent - target))

  const clipped = spent !== null && spent > scale
  const hWithin = spent === null ? 0 : Math.max(spent ? 2 : 0, (within / scale) * TRACK_H)
  const hAbove = spent === null ? 0 : Math.min((above / scale) * TRACK_H, TRACK_H - hWithin)
  const faded = m.state === 'partial' ? 0.55 : 1

  return (
    <div className="flex min-w-0 flex-1 flex-col items-center gap-1">
      <div className="money h-3 text-[8px] leading-none tabular-nums text-ink-3">
        {clipped ? shortINR(spent as number) : ''}
      </div>
      <div className="relative w-full max-w-[46px]" style={{ height: TRACK_H }}>
        {/* A period that has not happened draws NOTHING. A zero-height box reads as a broken sliver
            rather than as absence, and the month's label below already holds its place. */}
        {spent !== null && (
          <>
            <div className="absolute inset-x-0 bottom-0"
                 style={{ height: hWithin, background: 'var(--accent)', opacity: faded }} />
            {above > 0 && (
              <div
                className="absolute inset-x-0 rounded-t-[3px]"
                style={{
                  bottom: hWithin, height: hAbove,
                  background: 'var(--negative)', opacity: faded,
                  clipPath: clipped
                    ? 'polygon(0 5px,20% 0,40% 5px,60% 0,80% 5px,100% 0,100% 100%,0 100%)'
                    : undefined,
                }}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

/**
 * The month labels, as a SIBLING row rather than the foot of each bar.
 *
 * The handoff is specific about this and it is not cosmetic: the target rule is positioned at
 * `bottom: TRACK_H / 2` inside a track, so the track must contain the bars and NOTHING else. While
 * the label lived inside the column, the bar row's bottom edge was the bottom of the labels, and the
 * overlay compensated with `bottom: 18` — a hand-tuned number that was wrong by 10px, putting the
 * rule above half-track and through the overshoot caps. Same flex and gap as the bars, so the
 * columns line up by construction rather than by a matching guess.
 */
function MonthLabels({ months }: { months: YearMonth[] }) {
  return (
    <div className="mt-1 flex gap-1" aria-hidden>
      {months.map((m) => (
        <div key={m.month} className="min-w-0 flex-1 text-center text-[9px] leading-tight text-ink-3">
          {monthLetter(m.month)}
        </div>
      ))}
    </div>
  )
}

/**
 * The target, drawn ONCE across the whole chart and ON TOP of the bars.
 *
 * It was originally one dashed rule per column, painted behind its own bar. Every month from
 * January to July is over target, so each bar covered its own reference completely and the line
 * only survived where there was no data — visible in exactly the months it had nothing to say
 * about. A reference you cannot see next to the thing it references is decoration.
 *
 * One line works for all twelve columns even when their targets differ, because each bar is scaled
 * to its OWN target: half height always means "spent exactly the target", whatever that month's
 * target was.
 */
// translate-y-1/2 so the row's CENTRE lands on TRACK_H/2, not its bottom edge. `bottom: 66` alone
// anchors the bottom of a 14px-tall row (the "target" chip sets that height) and the rule is centred
// inside it, so the line drew 7px high — half the chip. Restructuring the track fixed the other
// 10px; this is the rest of the same error.
function TargetLine() {
  return (
    <div className="pointer-events-none absolute inset-x-0 flex translate-y-1/2 items-center"
         style={{ bottom: TRACK_H / CEILING }} aria-hidden>
      {/* An explicit colour, not `border-ink/45`: that class emits no rule at all (the palette is
          var()-based and Tailwind 3.4 cannot alpha a var), so the rule has been drawing in the
          default border colour, not 45% ink. See BLOCKED.md. */}
      <div className="flex-1 border-t border-dashed" style={{ borderColor: 'color-mix(in srgb, var(--ink) 45%, transparent)' }} />
      <span className="tracked ml-1.5 shrink-0 bg-surface px-1 text-[9px] text-ink-2">target</span>
    </div>
  )
}

function Verdict({ y, setAside, brokeIn }: {
  y: YearResponse
  /** How many purchases the household has set aside, and which period the envelope then breaks in.
   *  Both are needed here because the headline is the first thing that must react to a tick. */
  setAside: number
  brokeIn: string | null
}) {
  const times = y.envelope_inr ? y.net_spent_inr / y.envelope_inr : 0
  const over = y.envelope_inr > 0 && y.net_spent_inr > y.envelope_inr
  return (
    <Card>
      <div className="tracked text-[11px] text-ink-3">
        {setAside ? `Spent so far, less ${setAside} set aside` : 'Spent so far'}
      </div>
      <div className={`money mt-1 text-[clamp(2.25rem,11vw,3.25rem)] font-bold leading-none ${
        over ? 'text-negative' : 'text-ink'}`}>
        {shortINR(y.net_spent_inr)}
      </div>
      <div className="mt-2 text-sm text-ink-2">
        against <span className="money font-semibold text-ink">{shortINR(y.envelope_inr)}</span>{' '}
        budgeted for the year
        {y.envelope_inr > 0 && (
          <span className="money font-semibold text-ink-2"> · {times.toFixed(1)}×</span>
        )}
      </div>

      {/* The split that needs no threshold: min and max against each month's own target. The two
          add back up to the figure above them, which is what makes it arguable-with. */}
      {y.envelope_inr > 0 && (
        <div className="mt-4 flex overflow-hidden rounded-xl border border-line">
          <div className="flex-1 border-r border-line px-3 py-2.5">
            <div className="tracked text-[10px] text-ink-3">Inside the targets</div>
            <div className="money mt-0.5 text-lg font-bold text-accent">
              {shortINR(y.within_target_inr)}
            </div>
          </div>
          <div className="flex-1 px-3 py-2.5">
            <div className="tracked text-[10px] text-ink-3">Over them</div>
            <div className="money mt-0.5 text-lg font-bold text-negative">
              {shortINR(y.above_target_inr)}
            </div>
          </div>
        </div>
      )}

      {/* Without this line an early year reads as a catastrophe when the real cause is that nobody
          had started budgeting. Spend in a period with no target has no line to be inside of, so
          all of it lands on the right-hand figure above. */}
      {y.unbudgeted_periods > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-ink-3">
          {y.unbudgeted_periods} of these {y.mode === 'cycle' ? 'cycles' : 'months'} had no target
          set, so everything spent in them counts as over. That is missing budget, not overspend.
        </p>
      )}

      {y.exhausted && (
        <div className="mt-4 border-t border-line pt-4">
          <p className="text-[15px] leading-snug text-ink">
            The whole year&rsquo;s budget was gone by{' '}
            <span className="font-bold">{dayMon(y.exhausted.date)}</span>.
          </p>
          <p className="mt-1 truncate text-xs text-ink-3">
            The purchase that crossed it:{' '}
            <span className="text-ink-2">
              {merchantLabel(y.exhausted.description || y.exhausted.merchant)}
            </span>
            , <span className="money tabular-nums">{formatINR(y.exhausted.amount_inr)}</span>
          </p>
        </div>
      )}
      {/* With purchases set aside the exact crossing transaction is no longer the one the server
          found, and only monthly totals are known here — so this answers at period resolution
          rather than inventing a date. The genuinely useful case is the third line: setting a few
          things aside can turn a blown year into one that held, and that is the whole reason the
          list is tickable. */}
      {!y.exhausted && y.envelope_inr > 0 && (
        <div className="mt-4 border-t border-line pt-4 text-[15px] leading-snug text-ink">
          {brokeIn ? (
            <>
              With those set aside, the budget still ran out in{' '}
              <span className="font-bold">{monthLetter(brokeIn)}</span>.
            </>
          ) : setAside ? (
            <>
              With those set aside, the year stays inside its budget —{' '}
              <span className="money font-bold">{shortINR(y.envelope_inr - y.net_spent_inr)}</span>{' '}
              of it unspent.
            </>
          ) : (
            <>
              Still inside the year&rsquo;s budget, with{' '}
              <span className="money font-bold">{shortINR(y.envelope_inr - y.net_spent_inr)}</span>{' '}
              of it left.
            </>
          )}
        </div>
      )}
    </Card>
  )
}

function Outlook({ y }: { y: YearResponse }) {
  const o = y.outlook
  if (!o.months_closed) return null
  const impossible = o.needed_per_month_inr !== null && o.needed_per_month_inr < 0
  return (
    <Card>
      <SectionHeader level={2} title="Where the year lands" />
      <p className="mt-3 text-sm leading-relaxed text-ink-2">
        {o.months_closed} closed {o.months_closed === 1 ? 'period' : 'periods'} averaged{' '}
        <span className="money font-semibold text-ink">{formatINR(o.rate_inr)}</span>. Holding that
        for the {o.months_remaining} still to come puts the year at{' '}
        <span className="money font-semibold text-ink">{shortINR(o.year_end_inr)}</span>.
      </p>
      <p className="mt-2 text-sm leading-relaxed text-ink-2">
        {impossible ? (
          <>
            For the envelope to hold, those {o.months_remaining} periods would have to come to{' '}
            <span className="money font-semibold text-negative">
              {formatINR(o.needed_per_month_inr as number)}
            </span>{' '}
            a month. A negative number is the answer: nothing spent from here brings the year back
            under.
          </>
        ) : o.needed_per_month_inr !== null ? (
          <>
            To finish inside the envelope, the remaining {o.months_remaining} periods have{' '}
            <span className="money font-semibold text-ink">
              {formatINR(o.needed_per_month_inr)}
            </span>{' '}
            a month between them.
          </>
        ) : null}
      </p>
    </Card>
  )
}

export default function Year() {
  // The financial year may open in April, so "which year are we in" is a question only the server
  // can answer. Starting at null asks for the current one and adopts whatever comes back; the
  // stepper then walks from there. Seeding it with new Date().getFullYear() would have shown an
  // April household a year that has not started, for three months of every year.
  const [year, setYear] = useState<number | null>(null)
  const [excluded, setExcluded] = useState<Set<number>>(new Set())
  const [period] = usePeriod()
  const q = useQuery({
    queryKey: ['year', year, period],
    queryFn: () => fetchYear(year ?? undefined, period),
  })
  // The newest year that exists, learned from the first response rather than from the device clock,
  // so "next" stops at the current financial year instead of at 31 December.
  const [latest, setLatest] = useState<number | null>(null)
  useEffect(() => {
    if (latest === null && q.data) setLatest(q.data.year)
  }, [q.data, latest])

  /**
   * Setting a purchase aside has to move EVERY figure on the screen, not one.
   *
   * The first version only changed the per-month line inside its own card, so ticking the car left
   * the headline, the inside/over split and all twelve bars exactly where they were. A control that
   * changes one number out of five does not read as a filter, it reads as broken — and it was.
   *
   * Everything below is recomputed from the same arithmetic the server uses, which is why it can be
   * done here at all: subtract the excluded amounts from their own months, then within/above is
   * still min and max against that month's own target.
   */
  const raw = q.data
  const view = useMemo(() => {
    if (!raw) return null
    const off: Record<string, number> = {}
    for (const p of raw.top_purchases) {
      if (excluded.has(p.id)) off[p.month] = (off[p.month] ?? 0) + p.amount_inr
    }
    if (!excluded.size) return raw

    let cumulative = 0
    const months = raw.months.map((m) => {
      if (m.net_spent_inr === null) return m
      const net = Math.max(0, m.net_spent_inr - (off[m.month] ?? 0))
      cumulative += net
      const t = m.target_inr || 0
      return {
        ...m,
        net_spent_inr: net,
        cumulative_inr: cumulative,
        within_target_inr: t ? Math.min(net, t) : 0,
        above_target_inr: t ? Math.max(0, net - t) : net,
      }
    })
    const seen = months.filter((m) => m.net_spent_inr !== null)
    return {
      ...raw,
      months,
      net_spent_inr: seen.reduce((a, m) => a + (m.net_spent_inr || 0), 0),
      within_target_inr: seen.reduce((a, m) => a + (m.within_target_inr || 0), 0),
      above_target_inr: seen.reduce((a, m) => a + (m.above_target_inr || 0), 0),
      // The server found the exact transaction that crossed the envelope by walking the whole
      // ledger. Once purchases are set aside that transaction is no longer the one, and only the
      // twelve monthly totals are known here — so the answer drops to the period it happened in
      // rather than inventing a date it cannot see.
      exhausted: null,
    } as typeof raw
  }, [raw, excluded])

  const y = view
  /** Which period the envelope runs out in, once exclusions are applied. Month resolution. */
  const brokeIn = useMemo(() => {
    if (!y || !excluded.size || !y.envelope_inr) return null
    return y.months.find((m) => (m.cumulative_inr ?? 0) >= y.envelope_inr)?.month ?? null
  }, [y, excluded])

  const rest = useMemo(() => {
    if (!y) return null
    const closed = y.months.filter((m) => m.state === 'closed')
    if (!closed.length) return null
    const spent = closed.reduce((a, m) => a + (m.net_spent_inr || 0), 0)
    const target = closed.reduce((a, m) => a + m.target_inr, 0) / closed.length
    const off = (raw?.net_spent_inr ?? 0) - (y.net_spent_inr ?? 0)
    return { perMonth: spent / closed.length, target, off, n: closed.length }
  }, [y, raw])

  const toggle = (id: number) =>
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const header = (
    <SectionHeader
      eyebrow="The year"
      title={q.data?.label ?? '—'}
      sub="Twelve monthly targets, added up. Monthly targets never move — this only reports whether their sum holds."
      action={
        <div className="flex flex-wrap items-center gap-2">
        <PeriodToggle />
        <div className="inline-flex rounded-xl border border-line p-0.5">
          <button
            onClick={() => { setYear((v) => (v ?? q.data?.year ?? 0) - 1); setExcluded(new Set()) }}
            aria-label="Previous year"
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-ink-2 hover:text-ink"
          >
            <Icon name="chevron" size={18} className="rotate-180" />
          </button>
          <button
            onClick={() => { setYear((v) => (v ?? q.data?.year ?? 0) + 1); setExcluded(new Set()) }}
            disabled={latest === null || (year ?? latest) >= latest}
            aria-label="Next year"
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-ink-2 hover:text-ink disabled:opacity-30"
          >
            <Icon name="chevron" size={18} />
          </button>
        </div>
        </div>
      }
    />
  )

  if (q.isLoading) return <PageContainer>{header}<Loading rows={4} /></PageContainer>
  if (q.isError || !y)
    return (
      <PageContainer>
        {header}
        <ErrorState message="Couldn’t load the year." onRetry={() => q.refetch()} />
      </PageContainer>
    )

  const seen = y.months.filter((m) => m.state !== 'future')
  if (!seen.length || (!y.net_spent_inr && !y.envelope_inr))
    return (
      <PageContainer>
        {header}
        <Card>
          <p className="py-8 text-center text-sm text-ink-3">Nothing recorded for {q.data?.label ?? 'this year'} yet.</p>
        </Card>
      </PageContainer>
    )

  return (
    <PageContainer>
      {header}

      <Verdict y={y} setAside={excluded.size} brokeIn={brokeIn} />

      <Card>
        <SectionHeader
          level={2}
          title="Month by month"
          sub="Green is what fitted inside that month’s target, red is what went over it. A cut-off bar prints its real figure above."
        />
        {/* The bars sit in the flow; the target rides above them, anchored to the same track.
            The bar row now ends at the bottom of the bars, so the overlay is `bottom: 0` and
            `TRACK_H / 2` resolves to half-track with no offset to get wrong. Labels are the sibling
            row below. */}
        <div className="mt-4">
          <div className="relative">
            <div className="flex items-end gap-1">
              {y.months.map((m) => <Bar key={m.month} m={m} />)}
            </div>
            <div className="pointer-events-none absolute inset-x-0 bottom-0" style={{ height: TRACK_H }}>
              <TargetLine />
            </div>
          </div>
          <MonthLabels months={y.months} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-3">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm" style={{ background: 'var(--accent)' }} />
            inside the target
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm" style={{ background: 'var(--negative)' }} />
            over it
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm opacity-55" style={{ background: 'var(--accent)' }} />
            still running
          </span>
        </div>
      </Card>

      <Card>
        <SectionHeader
          level={2}
          title="What the year went on"
          sub="Tap anything you consider a one-off and the whole page re-totals without it — the headline, the split and every bar. You decide what counts as exceptional, not a rule you can’t see."
        />

        {rest && (
          <div className="mt-4 rounded-xl bg-surface-2 p-4">
            <div className="text-xs text-ink-2">
              {excluded.size === 0
                ? `All ${y.purchase_count.toLocaleString('en-IN')} purchases, over ${rest.n} closed ${rest.n === 1 ? 'period' : 'periods'}`
                : `Excluding ${excluded.size} ${excluded.size === 1 ? 'purchase' : 'purchases'} (${shortINR(rest.off)}), over ${rest.n} closed ${rest.n === 1 ? 'period' : 'periods'}`}
            </div>
            <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2">
              <span className={`money text-2xl font-bold ${
                rest.perMonth > rest.target ? 'text-negative' : 'text-accent'}`}>
                {formatINR(Math.round(rest.perMonth))}
              </span>
              <span className="text-sm text-ink-2">
                a month against a{' '}
                <span className="money font-semibold text-ink">
                  {formatINR(Math.round(rest.target))}
                </span>{' '}
                target
              </span>
            </div>
          </div>
        )}

        <ul className="mt-2">
          {y.top_purchases.map((p) => {
            const off = excluded.has(p.id)
            return (
              <li key={p.id}>
                <button
                  onClick={() => toggle(p.id)}
                  aria-pressed={off}
                  className="flex min-h-11 w-full items-center gap-3 border-b border-line px-1 py-3 text-left transition-colors last:border-0 hover:bg-surface-2"
                >
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
                      off ? 'border-accent bg-accent text-white' : 'border-line-2'}`}
                    aria-hidden
                  >
                    {off && <Icon name="check" size={13} />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={`block truncate text-sm font-medium ${
                      off ? 'text-ink-3 line-through' : 'text-ink'}`}>
                      {merchantLabel(p.description || p.merchant) || 'Purchase'}
                    </span>
                    <span className="block truncate text-xs text-ink-3">
                      {dayMon(p.date)} · {p.category}
                    </span>
                  </span>
                  <span className={`money shrink-0 text-right text-sm font-semibold tabular-nums ${
                    off ? 'text-ink-3' : 'text-ink'}`}>
                    {formatINR(p.amount_inr)}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      </Card>

      <Outlook y={y} />
    </PageContainer>
  )
}
