import { useState, type ReactNode } from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'

import {
  fetchDisposals,
  fetchInsurance,
  fetchNetWorthComposition,
  fetchNetWorthSummary,
  fetchNetworth,
  fetchRecurring,
  formatINR,
  shortINR,
  type NetWorthBucket,
  type RecurringInvestment,
} from '../api/client'
import {
  Card,
  Chip,
  EmptyState,
  ErrorState,
  Icon,
  LedgerList,
  LedgerRow,
  Loading,
  PageContainer,
  SectionHeader,
  type IconName,
} from '../components/ui'
import { fundStats, isFund } from '../lib/funds'
import { KIND_LABEL } from '../components/RecurringManager'

/**
 * Wealth — what you are worth, what it is made of, and where your funds stand. In that order,
 * because that is the order the question is actually asked.
 *
 * THE RULE THIS SCREEN EXISTS TO KEEP: the Wealth tab owns ZERO forms. It used to stack six CRUD
 * managers on one scroll — fifteen stat cards and fifteen forms, all arguing at the same volume,
 * so the one number a household opens the app for was buried under the machinery for editing it.
 * Every manager now lives behind its own ledger row at the bottom (see pages/WealthLedgers.tsx).
 * Nothing was deleted; adding and correcting simply became a destination instead of the loudest
 * thing on the screen.
 */

/**
 * Band colours come from the app's own category palette (tailwind.config.js `cat-1…cat-8`), taken
 * in order. `cat-6` (plum) is deliberately absent — purple reads as generated, not designed.
 */
const BAND_COLORS = [
  '#0F7A52', // cat-1 green
  '#2E728A', // cat-2 teal
  '#B07D1E', // cat-3 gold
  '#A6704A', // cat-4 clay
  '#6E8B5A', // cat-5 sage
  '#5B6472', // cat-7 slate
  '#C9A24B', // cat-8 sand
]
const bandColor = (i: number) => BAND_COLORS[i % BAND_COLORS.length]

function asOfLabel(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** The NAV feed dates as DD-MM-YYYY, which `new Date` cannot read. Everything else here is ISO. */
function navDate(raw?: string | null): Date | null {
  if (!raw) return null
  const dmy = raw.match(/^(\d{2})-(\d{2})-(\d{4})$/)
  const d = dmy ? new Date(+dmy[3], +dmy[2] - 1, +dmy[1]) : new Date(raw)
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * A NAV is struck after the market closes and published late in the evening, so yesterday's date is
 * NORMAL, not stale, and a weekend or holiday legitimately holds it back further. Naming the date is
 * always honest; calling it stale is only honest past four days.
 */
function navFreshness(funds: RecurringInvestment[]): { label: string; stale: boolean } | null {
  const dates = funds.map((f) => navDate(f.nav_date)).filter((d): d is Date => d !== null)
  if (dates.length === 0) return null
  const newest = new Date(Math.max(...dates.map((d) => d.getTime())))
  const days = Math.floor((Date.now() - newest.getTime()) / 86_400_000)
  return {
    label: newest.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }),
    stale: days > 4,
  }
}

/** A signed figure that must never render "+₹0" as if something happened. */
function signed(n: number): string {
  if (n > 0) return `+${formatINR(n)}`
  return formatINR(n)
}

/** The one overline token this screen uses. One size, everywhere. */
function Overline({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`tracked text-[11px] font-semibold text-ink-3 ${className}`}>{children}</div>
}

/** The single "estimate, not a live price" marker, used at the top and again per band. */
function EstimateChip() {
  return (
    <Chip color="var(--warn)" className="uppercase tracking-wider">
      Estimate
    </Chip>
  )
}

/* ─────────────────────────────  2 · HERO  +  3 · NO TREND YET  ───────────────────────────── */

/* ─────────────────────────────  4 · THE HONESTY LINE  ───────────────────────────── */

/* ─────────────────────────────  5 · WHAT IT'S MADE OF  ───────────────────────────── */

/**
 * The fourth state this app actually has: a real figure that is a projection. `Locked & accruing` is
 * a balance plus a contractual rate compounded forward, so it opens to show which accounts it is and
 * what each one accrues at, rather than asking the reader to trust one number.
 *
 * The rate shown per group is value-weighted, because a blended rate is what the group's own figure
 * was grown at. Averaging the rates flat would quote a number nothing was computed with.
 */
function LockedBreakdown({ investments }: { investments: RecurringInvestment[] }) {
  const navigate = useNavigate()
  const groups = new Map<string, { value: number; rated: number; count: number }>()
  for (const i of investments) {
    if (isFund(i)) continue
    const g = groups.get(i.kind) ?? { value: 0, rated: 0, count: 0 }
    g.value += i.current_value
    g.rated += (i.annual_rate_pct ?? 0) * i.current_value
    g.count += 1
    groups.set(i.kind, g)
  }
  const rows = [...groups.entries()]
    .map(([kind, g]) => ({
      kind,
      label: KIND_LABEL[kind] ?? kind.toUpperCase(),
      value: g.value,
      count: g.count,
      rate: g.value > 0 ? g.rated / g.value : 0,
    }))
    .sort((a, b) => b.value - a.value)
  if (rows.length === 0) return null
  return (
    <div className="border-t border-line bg-surface-2 px-4 py-3 sm:px-5">
      <p className="text-xs text-ink-2">Accrued, not confirmed. Each balance is grown at its own rate.</p>
      <div className="mt-2 space-y-1.5">
        {rows.map((r) => (
          <div key={r.kind} className="flex items-baseline gap-3">
            <span className="min-w-0 flex-1 truncate text-xs text-ink">
              {r.label}
              {r.count > 1 && (
                <span className="text-ink-3">
                  {' · '}
                  <span className="money">{r.count}</span> accounts
                </span>
              )}
              {r.rate > 0 && <span className="money text-ink-3">{` @ ${r.rate.toFixed(2)}%`}</span>}
            </span>
            <span className="money shrink-0 text-xs font-semibold text-ink">{shortINR(r.value)}</span>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => navigate('/wealth/investments')}
        className="mt-2 min-h-11 text-xs font-semibold text-accent underline-offset-2 hover:underline"
      >
        Update a balance
      </button>
    </div>
  )
}

function Composition({
  buckets,
  investments,
}: {
  buckets: NetWorthBucket[]
  investments: RecurringInvestment[]
}) {
  const [open, setOpen] = useState(false)
  return (
    <Card padded={false}>
      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="text-base font-bold text-ink">What it&apos;s made of</h2>
          <span className="text-xs text-ink-3">by nature, not by account</span>
        </div>
        <div
          className="mt-4 flex h-3.5 gap-0.5 overflow-hidden rounded-chip"
          role="img"
          aria-label={buckets.map((b) => `${b.label} ${Math.round(b.pct)}%`).join(', ')}
        >
          {buckets.map((b, i) => (
            <div key={b.key} style={{ flex: `${Math.max(b.pct, 0.5)} 1 0`, background: bandColor(i) }} />
          ))}
        </div>
      </div>

      <div>
        {buckets.map((b, i) => {
          // Only the accruing bucket has a breakdown worth opening: it is the one whose figure is a
          // projection rather than a price, so it is the one that owes an explanation.
          const expandable = b.key === 'locked' && investments.length > 0
          const Row = expandable ? 'button' : 'div'
          return (
            <div key={b.key}>
              <Row
                {...(expandable
                  ? {
                      type: 'button' as const,
                      onClick: () => setOpen((v) => !v),
                      'aria-expanded': open,
                    }
                  : {})}
                // 52px band rows, per the handoff. min-h-14 (56) plus py-2.5 landed them at 59–60,
                // which is the ledger row's 60 — and a composition band is not a ledger row, it is
                // one of five, so at the same height the two lists read as the same kind of thing.
                className={`flex w-full min-h-[52px] items-center gap-3 border-t border-line px-4 py-1.5 text-left sm:px-5 ${
                  expandable ? 'transition-colors hover:bg-surface-2' : ''
                }`}
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-chip"
                  style={{ background: bandColor(i) }}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="truncate text-sm font-semibold text-ink">{b.label}</span>
                    {!b.live_priced && <EstimateChip />}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-ink-3">
                    {b.sublabel} · <span className="money">{b.count}</span>{' '}
                    {b.count === 1 ? 'holding' : 'holdings'}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="money text-sm font-semibold text-ink">{shortINR(b.value_inr)}</div>
                  <div className="money mt-0.5 text-xs text-ink-3">{b.pct.toFixed(1)}%</div>
                </div>
                {expandable && (
                  <Icon
                    name="chevron"
                    size={16}
                    className={`shrink-0 text-ink-3 transition-transform ${open ? '-rotate-90' : 'rotate-90'}`}
                  />
                )}
              </Row>
              {expandable && open && <LockedBreakdown investments={investments} />}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

/* ─────────────────────────────  6 · MUTUAL FUNDS  ───────────────────────────── */

function FundTile({ label, value, tone = 'text-ink' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 bg-surface-2 px-3 py-2.5">
      <Overline>{label}</Overline>
      <div className={`money mt-1 truncate text-base font-bold ${tone}`}>{value}</div>
    </div>
  )
}

function FundsCard({ investments }: { investments: RecurringInvestment[] }) {
  const navigate = useNavigate()
  const s = fundStats(investments)

  if (s.funds.length === 0) {
    return (
      <Card>
        <h2 className="text-base font-bold text-ink">Mutual funds</h2>
        <div className="mt-4">
          <EmptyState
            icon="chart"
            title="No funds tracked yet"
            hint="Add one SIP and this card starts answering."
            action={{ label: 'Add a fund', onClick: () => navigate('/wealth/investments') }}
          />
        </div>
      </Card>
    )
  }

  const live = s.funds.some((f) => f.priced === 'live')
  const nav = navFreshness(s.funds)
  return (
    <Card padded={false}>
      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="text-base font-bold text-ink">Mutual funds</h2>
          {live && nav && (
            <span className={`flex items-center gap-1.5 text-xs ${nav.stale ? 'text-warn' : 'text-ink-3'}`}>
              <span className={`h-1.5 w-1.5 rounded-chip ${nav.stale ? 'bg-warn' : 'bg-accent'}`} />
              NAV <span className="money">{nav.label}</span>
            </span>
          )}
        </div>
        {nav?.stale && (
          <p className="mt-1 text-xs text-warn">
            That price is more than four days old, so these values are the last ones that came through.
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-end gap-x-3 gap-y-1">
          <div className="money text-3xl font-bold leading-none tracking-tight text-ink">
            {formatINR(s.value)}
          </div>
          <div
            className={`money pb-0.5 text-sm font-semibold ${
              s.gain < 0 ? 'text-negative' : 'text-positive'
            }`}
          >
            {signed(s.gain)}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-input border border-line bg-line">
          <FundTile
            label="XIRR"
            value={s.xirr === null ? '—' : `${s.xirr.toFixed(2)}%`}
            tone={s.xirr === null ? 'text-ink-3' : s.xirr < 0 ? 'text-negative' : 'text-positive'}
          />
          <FundTile label="SIP / mo" value={shortINR(s.sip)} />
          <FundTile
            label="Down"
            value={`${s.down} of ${s.funds.length}`}
            tone={s.down > 0 ? 'text-negative' : 'text-ink'}
          />
        </div>

        {s.xirr !== null && s.xirrCount < s.funds.length && (
          <p className="mt-2 text-xs text-ink-3">
            XIRR is value-weighted across the <span className="money">{s.xirrCount}</span> of{' '}
            <span className="money">{s.funds.length}</span> funds that report one.
          </p>
        )}
        {s.xirr === null && (
          <p className="mt-2 text-xs text-ink-3">
            No fund reports a realised return yet, so there is no XIRR to show.
          </p>
        )}
      </div>

      <Link
        to="/wealth/funds"
        className="flex min-h-14 items-center justify-between gap-3 border-t border-line bg-surface-2 px-4 py-3 text-ink transition-colors hover:bg-surface sm:px-5"
      >
        <span className="text-sm font-semibold">
          All <span className="money">{s.funds.length}</span> funds, holding by holding
        </span>
        <span className="flex shrink-0 text-accent">
          <Icon name="chevron" size={18} />
        </span>
      </Link>
    </Card>
  )
}

/* ─────────────────────────────  7 · NEEDS A LOOK  ───────────────────────────── */

type Attention = { key: string; icon: IconName; tone: string; title: ReactNode; sub: ReactNode; to: string }

function NeedsALook({ items }: { items: Attention[] }) {
  if (items.length === 0) return null
  return (
    <Card padded={false}>
      <Overline className="px-4 pb-2 pt-3.5 sm:px-5">Needs a look</Overline>
      {items.map((it) => (
        <Link
          key={it.key}
          to={it.to}
          className="flex min-h-14 items-center gap-3 border-t border-line px-4 py-2.5 text-ink transition-colors hover:bg-surface-2 sm:px-5"
        >
          <span className={`flex shrink-0 ${it.tone}`}>
            <Icon name={it.icon} size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-ink">{it.title}</div>
            <div className="mt-0.5 truncate text-xs text-ink-3">{it.sub}</div>
          </div>
          <span className="flex shrink-0 text-ink-3">
            <Icon name="chevron" size={16} />
          </span>
        </Link>
      ))}
    </Card>
  )
}

/* ─────────────────────────────  8 · THE LEDGER INDEX  ───────────────────────────── */

type LedgerRow = {
  key: string
  icon: IconName
  label: string
  sub: ReactNode
  value: string
  tone?: string
  to: string
  /** leaves the Wealth tab entirely — the row shows an arrow, not the ledger chevron */
  away?: boolean
}

function LedgerIndex({ rows }: { rows: LedgerRow[] }) {
  return (
    <section>
      <Overline className="px-1 pb-2.5">Open a ledger to add or correct</Overline>
      <LedgerList>
        {rows.map((r, i) => (
          <LedgerRow key={r.key} to={r.to} label={r.label} sub={r.sub} value={r.value}
                     icon={r.icon} tone={r.tone} away={r.away} first={i === 0} />
        ))}
      </LedgerList>
    </section>
  )
}

/* ─────────────────────────────  THE TAB  ───────────────────────────── */

/**
 * Loading / error / populated, without letting an error hide behind a spinner forever — and without
 * throwing away a figure we already have. A refresh that fails still leaves the last successful
 * response in the cache, so the block keeps answering and says the answer is not fresh. Replacing a
 * real number with an error panel because the LATEST fetch failed tells you less than the stale
 * number does.
 */
function Block<T>({
  q,
  rows = 3,
  label,
  children,
}: {
  q: UseQueryResult<T>
  rows?: number
  label: string
  children: (data: T) => ReactNode
}) {
  if (q.isError && !q.data) {
    return <ErrorState message={(q.error as Error)?.message} onRetry={() => q.refetch()} />
  }
  if (!q.data) return <Loading rows={rows} label={label} />
  return (
    <>
      {q.isError && <StaleNotice onRetry={() => q.refetch()} />}
      {children(q.data)}
    </>
  )
}

/** Sits above a figure that is real but no longer confirmed. */
function StaleNotice({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-input border border-line bg-surface-2 px-3 py-2">
      <span className="text-xs text-ink-2">
        Could not refresh just now. The figures below are the last ones that came through.
      </span>
      <button
        type="button"
        onClick={onRetry}
        className="min-h-11 text-xs font-semibold text-accent underline-offset-2 hover:underline"
      >
        Try again
      </button>
    </div>
  )
}

export default function Savings() {
  const navigate = useNavigate()

  const comp = useQuery({ queryKey: ['networth-composition'], queryFn: fetchNetWorthComposition })
  const summary = useQuery({ queryKey: ['networth-summary'], queryFn: fetchNetWorthSummary })
  const recurring = useQuery({ queryKey: ['recurring'], queryFn: () => fetchRecurring() })
  // Same key shape AssetManager uses, so the ledger row and the manager share one cache entry.
  const assets = useQuery({ queryKey: ['networth', false], queryFn: () => fetchNetworth() })
  const insurance = useQuery({ queryKey: ['insurance'], queryFn: fetchInsurance })
  const disposals = useQuery({ queryKey: ['disposals'], queryFn: fetchDisposals })

  const asOf = asOfLabel(comp.data?.as_of)

  // ── 7 · the two conditions, both derived. Neither section renders unless its condition holds. ──
  const attention: Attention[] = []
  const gold = summary.data?.gold_collateral
  const goldLoanBalance = (gold?.gold_loans ?? []).reduce((s, l) => s + l.balance, 0)
  if (gold && gold.pledged_value > 0 && goldLoanBalance > 0) {
    attention.push({
      key: 'gold-pledged',
      icon: 'wallet',
      tone: 'text-warn',
      title: <><span className="money">{shortINR(gold.pledged_value)}</span> of gold is pledged</>,
      sub: <>against a <span className="money">{shortINR(goldLoanBalance)}</span> loan</>,
      to: '/debt',
    })
  }
  const dormant = (recurring.data?.investments ?? []).filter(
    (r) => isFund(r) && r.current_value > 0 && (!r.active || r.monthly_contribution <= 0),
  )
  if (dormant.length > 0) {
    const held = dormant.reduce((s, f) => s + f.current_value, 0)
    attention.push({
      key: 'sip-off',
      icon: 'chart',
      tone: 'text-ink-3',
      title: (
        <>
          <span className="money">{dormant.length}</span>{' '}
          {dormant.length === 1 ? 'fund is held' : 'funds held'} with SIP off
        </>
      ),
      sub: <><span className="money">{shortINR(held)}</span> still invested, nothing going in</>,
      to: '/wealth/funds',
    })
  }

  // ── 8 · the six domains ──
  const assetTypes = [...new Set((assets.data?.assets ?? []).map((a) => a.asset_type))]
  const recKinds = [...new Set((recurring.data?.investments ?? []).map((r) => r.kind.toUpperCase()))]
  const disposalRows = disposals.data?.disposals ?? []
  const gains = disposalRows.filter((d) => d.gain_inr !== null)
  const gainTotal = gains.reduce((s, d) => s + (d.gain_inr as number), 0)

  const ledgerReady =
    assets.data && recurring.data && summary.data && insurance.data && disposals.data
  const ledgerError = assets.isError || recurring.isError || summary.isError || insurance.isError || disposals.isError

  const ledgerRows: LedgerRow[] = ledgerReady
    ? [
        {
          key: 'assets',
          icon: 'home',
          label: 'Assets',
          sub: (
            <>
              <span className="money">{assets.data.assets.length}</span> items
              {assetTypes.length > 0 && ` · ${assetTypes.slice(0, 4).join(', ')}`}
            </>
          ),
          value: shortINR(assets.data.total_inr),
          to: '/wealth/assets',
        },
        {
          key: 'investments',
          icon: 'chart',
          label: 'Recurring investments',
          sub: (
            <>
              <span className="money">{recurring.data.investments.length}</span> instructions
              {recKinds.length > 0 && ` · ${recKinds.join(', ')}`}
            </>
          ),
          value: shortINR(recurring.data.total_value),
          to: '/wealth/investments',
        },
        {
          key: 'gold',
          icon: 'wallet',
          label: 'Gold',
          sub: (
            <>
              <span className="money">{summary.data.gold_collateral?.total_grams ?? 0}</span>g
              {(summary.data.gold_collateral?.pledged_value ?? 0) > 0 && (
                <>
                  {' · '}
                  <span className="money">{shortINR(summary.data.gold_collateral.pledged_value)}</span>
                  {' pledged'}
                </>
              )}
            </>
          ),
          value: shortINR(summary.data.gold_collateral?.real_value ?? 0),
          to: '/wealth/gold',
        },
        {
          key: 'insurance',
          icon: 'shield',
          label: 'Insurance',
          sub: (
            <>
              <span className="money">{insurance.data.policies.length}</span>{' '}
              {insurance.data.policies.length === 1 ? 'policy' : 'policies'} · cover, not an asset
            </>
          ),
          value: shortINR(insurance.data.total_cover),
          to: '/wealth/insurance',
        },
        {
          key: 'disposals',
          icon: 'list',
          label: 'Sales & capital gains',
          sub: (
            <>
              <span className="money">{disposalRows.length}</span>{' '}
              {disposalRows.length === 1 ? 'sale' : 'sales'}
              {gains.length < disposalRows.length && ' · some gains not computable'}
            </>
          ),
          value: gains.length > 0 ? signed(gainTotal) : '—',
          tone: gains.length === 0 ? 'text-ink-3' : gainTotal < 0 ? 'text-negative' : 'text-positive',
          to: '/wealth/disposals',
        },
        {
          // Not a wealth ledger. It lives on the Debt tab and always has — say so on the row rather
          // than let it read as a sixth ledger in the pattern above.
          key: 'liabilities',
          icon: 'card',
          label: 'Liabilities',
          sub: (
            <>
              <span className="money">{summary.data.liabilities.length}</span>{' '}
              {summary.data.liabilities.length === 1 ? 'loan' : 'loans'} · opens the Debt tab
            </>
          ),
          value: shortINR(summary.data.total_liabilities),
          tone: 'text-negative',
          to: '/debt',
          away: true,
        },
      ]
    : []

  return (
    <PageContainer gap="space-y-6">
      {/* 1 · header */}


      {/*
        Width buys parallelism, not more content. On a phone this is one column in the order the
        question is asked. From lg up the same components lay out side by side so the three answers
        stop being a scroll, and the ledger index moves to a rail — navigation belongs at the edge on
        a pointer device, not in the reading path. One component tree, responsive classes only: two
        trees would drift, which is how this screen got into the state it was in.
      */}
      <div className="lg:grid lg:grid-cols-12 lg:items-start lg:gap-6">
        <div className="space-y-6 lg:col-span-8">
          {/* 2–5 · worth, then shape */}
          <Block q={comp} rows={4} label="Working out what you are worth">
            {(c) =>
              c.buckets.length === 0 && c.total_assets === 0 ? (
                <EmptyState
                  icon="wallet"
                  title="Nothing tracked yet"
                  hint="Add what you own and this tab starts answering."
                  action={{ label: 'Open the assets ledger', onClick: () => navigate('/wealth/assets') }}
                />
              ) : (
                <div className="space-y-6 xl:grid xl:grid-cols-2 xl:items-start xl:gap-6 xl:space-y-0">
                  {/* The net-worth figure and the estimate line are Balance's hero, directly above
                      this. Rendered here as well they put Rs 1,50,00,000 on the screen twice under two
                      different headings, which is the duplication this merge existed to remove. */}
                  <Composition buckets={c.buckets} investments={recurring.data?.investments ?? []} />
                </div>
              )
            }
          </Block>

          {/* 6 · funds */}
          <Block q={recurring} rows={3} label="Loading your funds">
            {(r) => <FundsCard investments={r.investments} />}
          </Block>

          {/* 7 · only when a condition actually holds */}
          <NeedsALook items={attention} />
        </div>

        {/* 8 · every domain, one row each. All editing lives behind these. */}
        <div className="mt-6 lg:col-span-4 lg:mt-0 lg:sticky lg:top-6">
          {ledgerError && !ledgerReady ? (
            <ErrorState
              message="Couldn't load the ledger totals."
              onRetry={() => {
                void assets.refetch()
                void recurring.refetch()
                void summary.refetch()
                void insurance.refetch()
                void disposals.refetch()
              }}
            />
          ) : ledgerReady ? (
            <LedgerIndex rows={ledgerRows} />
          ) : (
            <Loading rows={3} label="Loading your ledgers" />
          )}
        </div>
      </div>
    </PageContainer>
  )
}
