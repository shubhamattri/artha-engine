import { useState, type ReactNode } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  currencySymbol,
  fetchRecurring,
  formatINR,
  pauseRecurring,
  type RecurringInvestment,
} from '../api/client'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import {
  Card,
  Chip,
  EmptyState,
  ErrorState,
  Icon,
  Loading,
  PageContainer,
  SectionHeader,
  useConfirm,
} from '../components/ui'
import { fundStats, isDown, isFund, sipOff } from '../lib/funds'
import { ViewOnly, useCan } from '../auth/Can'

/**
 * The funds answer does not fit on the Wealth tab, so it gets its own route.
 *
 * /wealth/funds        ranks every holding by value — the biggest position is the first thing read.
 * /wealth/funds/:id    is one fund: what it is worth, what it cost, and the instruction behind it.
 *
 * Both are read-first. Editing still lives in the ledger at /wealth/investments; the only write
 * offered here is stopping (or resuming) the standing instruction, because that is the decision a
 * household actually makes while looking at a fund.
 */

/* ─────────────────────────────  tokens & formatting  ───────────────────────────── */

function Overline({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`tracked text-[11px] font-semibold text-ink-3 ${className}`}>{children}</div>
}

/** A gain that must never render "+₹0" as if something happened. */
function signedMoney(n: number): string {
  return n > 0 ? `+${formatINR(n)}` : formatINR(n)
}

function signedPct(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return `${sign}${Math.abs(n).toFixed(1)}%`
}

const toneFor = (n: number) => (n < 0 ? 'text-negative' : n > 0 ? 'text-positive' : 'text-ink')

/**
 * A blank XIRR reads as broken, so say what it is waiting for. The backend only annualises once a
 * holding is 90 days past its anchor (api/recurring.py), and re-linking a fund to the broker resets
 * that anchor to the day it was linked — which is why every fund can go quiet at once.
 */
function XirrPending({ funds }: { funds: RecurringInvestment[] }) {
  const anchors = funds.map((f) => f.as_of_date).filter(Boolean).sort()
  if (anchors.length === 0) return null
  const days = Math.floor((Date.now() - new Date(anchors[0]).getTime()) / 86_400_000)
  const left = Math.max(0, 90 - days)
  return (
    <p className="mt-2 text-xs text-ink-3">
      No XIRR yet. It needs 90 days of tracked history and the oldest holding here was anchored{' '}
      <span className="money">{days}</span> {days === 1 ? 'day' : 'days'} ago, so the first figure is
      about <span className="money">{left}</span> {left === 1 ? 'day' : 'days'} away. Recording each
      fund&rsquo;s real purchases brings it forward; annualising a few weeks would be noise, not a return.
    </p>
  )
}

/** NAV carries paise. formatINR rounds to whole rupees, which would turn 277.28 into 277. */
function formatNav(n: number): string {
  return `${currencySymbol()}${n.toFixed(2)}`
}

function dateLabel(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function fullDateLabel(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

function monthYearLabel(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

/** The earliest dated point in the record — the opening balance's date, or the first ledger entry. */
function startedOn(f: RecurringInvestment): string | null {
  const dates = [f.as_of_date, ...(f.contributions ?? []).map((c) => c.date)].filter(Boolean)
  if (dates.length === 0) return null
  return dates.reduce((a, b) => (a < b ? a : b))
}

/** One cell of a hairline-split tile grid. Same token on both screens. */
function Tile({ label, value, tone = 'text-ink' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 bg-surface-2 p-3">
      <Overline>{label}</Overline>
      <div className={`money mt-1 truncate text-base font-bold ${tone}`}>{value}</div>
    </div>
  )
}

function LiveChip() {
  return (
    <span className="tracked flex min-h-8 items-center gap-2 rounded-chip border border-line bg-surface px-3 text-[10px] font-semibold text-ink-2">
      <span className="h-2 w-2 rounded-chip bg-accent" />
      Live
    </span>
  )
}

function OffChip() {
  return (
    <Chip className="tracked text-[10px] uppercase">SIP off · held</Chip>
  )
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="-ml-1 inline-flex min-h-11 items-center gap-1 rounded-input px-1 text-sm font-semibold text-ink-2 transition-colors hover:text-ink"
    >
      <Icon name="chevron" size={16} className="rotate-180" />
      {label}
    </Link>
  )
}

/* ─────────────────────────────  SCREEN 2 · THE PORTFOLIO  ───────────────────────────── */

type Filter = 'all' | 'down' | 'off'

export function FundsPortfolio() {
  const q = useQuery({ queryKey: ['recurring'], queryFn: () => fetchRecurring() })
  const navigate = useNavigate()
  const mayEdit = useCan('wealth', 'edit')
  const [filter, setFilter] = useState<Filter>('all')

  const header = (action?: ReactNode) => (
    <div>
      <BackLink to="/balance" label="Balance" />
      <SectionHeader title="Mutual funds" className="mt-1" action={action} />
    </div>
  )

  if (q.isError) {
    return (
      <PageContainer gap="space-y-4">
        {header()}
        <ErrorState message={(q.error as Error)?.message} onRetry={() => q.refetch()} />
      </PageContainer>
    )
  }
  if (!q.data) {
    return (
      <PageContainer gap="space-y-4">
        {header()}
        <Loading rows={4} label="Loading your funds" />
      </PageContainer>
    )
  }

  const s = fundStats(q.data.investments)

  if (s.funds.length === 0) {
    return (
      <PageContainer gap="space-y-4">
        {header()}
        <EmptyState
          icon="chart"
          title="No funds tracked yet"
          hint="Add one SIP and this screen starts answering."
          action={mayEdit ? { label: 'Open the investments ledger', onClick: () => navigate('/wealth/investments') } : undefined}
        />
      </PageContainer>
    )
  }

  // A filter chip that would empty the list is not offered, so the active filter is always live.
  const allChips: { key: Filter; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: s.funds.length },
    { key: 'down', label: 'Down', count: s.down },
    { key: 'off', label: 'SIP off', count: s.off },
  ]
  const chips = allChips.filter((c) => c.key === 'all' || c.count > 0)
  const active = chips.some((c) => c.key === filter) ? filter : 'all'

  const rows = s.funds
    .filter((f) => (active === 'down' ? isDown(f) : active === 'off' ? sipOff(f) : true))
    .sort((a, b) => b.current_value - a.current_value)

  const live = s.funds.some((f) => f.priced === 'live')

  return (
    <PageContainer gap="space-y-4">
      {header(live ? <LiveChip /> : undefined)}

      {/* the summary strip — value, what it cost, and the two rates that judge it */}
      <Card className="p-4 sm:p-5">
        <Overline>Current value</Overline>
        <div className="money mt-2 text-3xl font-bold leading-none tracking-tight text-ink sm:text-4xl">
          {formatINR(s.value)}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-ink-2">
          <span className={`money font-semibold ${toneFor(s.gain)}`}>{signedMoney(s.gain)}</span>
          <span>on</span>
          <span className="money font-semibold text-ink">{formatINR(s.contributed)}</span>
          <span>invested</span>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-input border border-line bg-line">
          <Tile
            label="Portfolio XIRR"
            value={s.xirr === null ? '—' : `${s.xirr.toFixed(2)}%`}
            tone={s.xirr === null ? 'text-ink-3' : toneFor(s.xirr)}
          />
          <Tile label="Going in monthly" value={formatINR(s.sip)} />
        </div>

        {s.xirr !== null && s.xirrCount < s.funds.length && (
          <p className="mt-2 text-xs text-ink-3">
            XIRR is value-weighted across the <span className="money">{s.xirrCount}</span> of{' '}
            <span className="money">{s.funds.length}</span> funds that report one.
          </p>
        )}
        {s.xirr === null && <XirrPending funds={s.funds} />}
      </Card>

      {/* filters */}
      <div className="flex flex-wrap gap-2">
        {chips.map((c) => (
          <button
            key={c.key}
            type="button"
            aria-pressed={active === c.key}
            onClick={() => setFilter(c.key)}
            className={`flex min-h-11 items-center rounded-chip px-4 text-xs font-semibold transition-colors ${
              active === c.key
                ? 'bg-accent text-white'
                : 'border border-line bg-surface text-ink-2 hover:border-line-2 hover:text-ink'
            }`}
          >
            {c.label}
            <span className="money ml-1">{c.count}</span>
          </button>
        ))}
      </div>

      {/* holdings, biggest first */}
      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 border-b border-line bg-surface-2 px-4 py-3">
          <Overline>Holding</Overline>
          <Overline>Value · return</Overline>
        </div>
        {rows.map((f, i) => (
          <Link
            key={f.id}
            to={`/wealth/funds/${f.id}`}
            className={`flex min-h-16 items-center gap-3 px-4 py-3 text-ink transition-colors hover:bg-surface-2 ${
              i > 0 ? 'border-t border-line' : ''
            }`}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-ink">{f.name}</div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {sipOff(f) ? (
                  <OffChip />
                ) : (
                  <span className="text-xs text-ink-3">
                    SIP <span className="money">{formatINR(f.monthly_contribution)}</span>/mo
                  </span>
                )}
              </div>
            </div>
            <div className="shrink-0 text-right">
              <div className="money text-sm font-semibold text-ink">{formatINR(f.current_value)}</div>
              <div
                className={`money mt-1 text-xs font-semibold ${
                  typeof f.return_pct === 'number' ? toneFor(f.return_pct) : 'text-ink-3'
                }`}
              >
                {typeof f.return_pct === 'number' ? signedPct(f.return_pct) : '—'}
              </div>
            </div>
            <span className="flex shrink-0 text-ink-3">
              <Icon name="chevron" size={16} />
            </span>
          </Link>
        ))}
      </Card>

      {mayEdit && (
        <Link
          to="/wealth/investments"
          className="flex min-h-12 items-center justify-center gap-2 rounded-input border border-line-2 bg-surface text-sm font-semibold text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
        >
          <Icon name="plus" size={17} />
          Add a fund or SIP
        </Link>
      )}
    </PageContainer>
  )
}

/* ─────────────────────────────  SCREEN 3 · ONE FUND  ───────────────────────────── */

function InstructionRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-h-14 items-center gap-3 border-t border-line px-4 py-2">
      <div className="min-w-0 flex-1 text-sm text-ink-2">{label}</div>
      <div className="money shrink-0 text-sm font-semibold text-ink">{value}</div>
    </div>
  )
}

export function FundDetail() {
  const { id } = useParams<{ id: string }>()
  const q = useQuery({ queryKey: ['recurring'], queryFn: () => fetchRecurring() })
  const refresh = useHouseholdRefresh()
  const confirm = useConfirm()
  const mayEdit = useCan('wealth', 'edit')
  const [showAll, setShowAll] = useState(false)

  const toggleSip = useMutation({
    mutationFn: ({ fundId, active }: { fundId: number; active: boolean }) =>
      pauseRecurring(fundId, active),
    onSuccess: refresh,
  })

  const header = (title: string, action?: ReactNode) => (
    <div>
      <BackLink to="/wealth/funds" label="Mutual funds" />
      <SectionHeader title={title} className="mt-1" action={action} />
    </div>
  )

  if (q.isError) {
    return (
      <PageContainer gap="space-y-4">
        {header('Fund')}
        <ErrorState message={(q.error as Error)?.message} onRetry={() => q.refetch()} />
      </PageContainer>
    )
  }
  if (!q.data) {
    return (
      <PageContainer gap="space-y-4">
        {header('Fund')}
        <Loading rows={4} label="Loading this fund" />
      </PageContainer>
    )
  }

  const f = q.data.investments.find((r) => String(r.id) === id && isFund(r))
  if (!f) {
    return (
      <PageContainer gap="space-y-4">
        {header('Fund')}
        <EmptyState
          icon="chart"
          title="That fund isn’t here"
          hint="It may have been removed, or the link is stale."
        />
      </PageContainer>
    )
  }

  const off = sipOff(f)
  const ledger = [...(f.contributions ?? [])].sort((a, b) => (a.date < b.date ? 1 : -1))
  const shown = showAll ? ledger : ledger.slice(0, 3)
  const started = startedOn(f)

  const stop = async () => {
    const ok = await confirm({
      title: 'Stop this SIP?',
      message: 'Nothing more goes in from next month. The units you already hold stay invested.',
      confirmLabel: 'Stop the SIP',
      danger: true,
    })
    if (ok) toggleSip.mutate({ fundId: f.id, active: false })
  }

  return (
    <PageContainer gap="space-y-4">
      {header(
        f.name,
        mayEdit ? (
          <Link
            to="/wealth/investments"
            aria-label="Edit in the investments ledger"
            className="flex h-11 w-11 items-center justify-center rounded-chip text-ink-2 transition-colors hover:bg-surface hover:text-ink"
          >
            <Icon name="edit" size={18} />
          </Link>
        ) : undefined,
      )}

      {/* what it is worth, and what the price behind that is */}
      <Card className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2">
          {off ? (
            <OffChip />
          ) : (
            <Chip color="var(--accent)" className="tracked text-[10px] uppercase">
              SIP active
            </Chip>
          )}
        </div>

        <Overline className="mt-4">Current value</Overline>
        <div className="money mt-2 text-3xl font-bold leading-none tracking-tight text-ink sm:text-4xl">
          {formatINR(f.current_value)}
        </div>
        {typeof f.nav === 'number' && f.nav > 0 && (
          <div className="mt-2 text-sm text-ink-2">
            NAV <span className="money font-semibold text-ink">{formatNav(f.nav)}</span>
            {f.nav_date && (
              <>
                {' '}
                as of <span className="money">{dateLabel(f.nav_date)}</span>
              </>
            )}
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-input border border-line bg-line">
          <Tile label="Invested" value={formatINR(f.total_contributed)} />
          <Tile
            label="Gain"
            value={signedMoney(f.interest_earned)}
            tone={toneFor(f.interest_earned)}
          />
          <Tile
            label="XIRR"
            value={typeof f.xirr_pct === 'number' ? `${f.xirr_pct.toFixed(1)}%` : '—'}
            tone={typeof f.xirr_pct === 'number' ? toneFor(f.xirr_pct) : 'text-ink-3'}
          />
          <Tile
            label="Units"
            value={typeof f.units === 'number' ? f.units.toFixed(2) : '—'}
            tone={typeof f.units === 'number' ? 'text-ink' : 'text-ink-3'}
          />
        </div>
      </Card>

      {/* the standing instruction behind the holding */}
      <Card padded={false}>
        <Overline className="px-4 pb-3 pt-4">Instruction</Overline>
        <InstructionRow
          label="Amount"
          value={f.monthly_contribution > 0 ? `${formatINR(f.monthly_contribution)} / mo` : '—'}
        />
        {started && <InstructionRow label="Started" value={monthYearLabel(started)} />}
      </Card>

      {/* the ledger — dates, the NAV each bought at, and the amount */}
      <Card padded={false}>
        <Overline className="px-4 pb-3 pt-4">Recent instalments</Overline>
        {ledger.length === 0 ? (
          <div className="border-t border-line px-4 py-8 text-center text-sm text-ink-2">
            No instalments recorded yet.
          </div>
        ) : (
          <>
            {shown.map((c, i) => {
              const amount = typeof c.amount === 'number' ? c.amount : null
              return (
                <div
                  key={`${c.date}-${i}`}
                  className="flex min-h-12 items-center gap-3 border-t border-line px-4 py-2"
                >
                  <div className="money min-w-0 flex-1 truncate text-xs text-ink-2">
                    {fullDateLabel(c.date)}
                  </div>
                  {typeof c.nav === 'number' && (
                    <div className="money shrink-0 text-xs text-ink-3">@ {formatNav(c.nav)}</div>
                  )}
                  <div
                    className={`money shrink-0 text-sm font-semibold ${
                      amount !== null && amount < 0 ? 'text-negative' : 'text-ink'
                    }`}
                  >
                    {amount !== null
                      ? formatINR(amount)
                      : typeof c.units === 'number'
                        ? `${c.units.toFixed(2)} units`
                        : '—'}
                  </div>
                </div>
              )
            })}
            {ledger.length > 3 && (
              <button
                type="button"
                onClick={() => setShowAll((v) => !v)}
                className="flex min-h-12 w-full items-center justify-center border-t border-line bg-surface-2 px-4 text-sm font-semibold text-accent transition-colors hover:text-accent-2"
              >
                {showAll ? (
                  'Show the last 3'
                ) : (
                  <>
                    All <span className="money mx-1">{ledger.length}</span> instalments
                  </>
                )}
              </button>
            )}
          </>
        )}
      </Card>

      {/* the two decisions this screen exists for */}
      {!mayEdit ? <ViewOnly what="this fund" /> : (
      <div className="flex flex-col gap-2">
        <Link
          to="/wealth/investments"
          className="flex min-h-12 items-center justify-center rounded-input bg-accent px-4 text-sm font-semibold text-white transition-colors hover:bg-accent-2"
        >
          Edit this SIP
        </Link>
        {off ? (
          <button
            type="button"
            disabled={toggleSip.isPending}
            onClick={() => toggleSip.mutate({ fundId: f.id, active: true })}
            className="flex min-h-12 items-center justify-center rounded-input border border-line-2 bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-surface-2 disabled:opacity-50"
          >
            {toggleSip.isPending ? 'Restarting…' : 'Restart the SIP'}
          </button>
        ) : (
          <button
            type="button"
            disabled={toggleSip.isPending}
            onClick={stop}
            className="flex min-h-12 items-center justify-center rounded-input border border-line-2 bg-surface px-4 text-sm font-semibold text-negative transition-colors hover:bg-surface-2 disabled:opacity-50"
          >
            {toggleSip.isPending ? 'Stopping…' : 'Stop SIP, keep units'}
          </button>
        )}
        {toggleSip.isError && (
          <p className="text-xs text-negative">
            Couldn’t change the instruction. {(toggleSip.error as Error)?.message}
          </p>
        )}
      </div>
      )}
    </PageContainer>
  )
}
