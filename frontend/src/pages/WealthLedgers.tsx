import { useId, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import AssetManager from '../components/AssetManager'
import DisposalsManager from '../components/Disposals'
import GoldCollateral from '../components/GoldCollateral'
import InsuranceManager, { KIND_LABEL } from '../components/InsuranceManager'
import RecurringManager, { KIND_LABEL as RECURRING_KIND_LABEL } from '../components/RecurringManager'
import {
  Card,
  Icon,
  LedgerBlock,
  LedgerRows,
  LedgerSummary,
  PageContainer,
  SectionHeader,
  type LedgerGroup,
  type LedgerRowItem,
} from '../components/ui'
import { isFund, sipOff } from '../lib/funds'
import {
  fetchDisposals,
  fetchInsurance,
  fetchNetWorthSummary,
  fetchNetworth,
  fetchRecurring,
  shortINR,
  type Asset,
  type RecurringInvestment,
} from '../api/client'

/**
 * The five wealth ledgers, each on its own route.
 *
 * The Wealth tab used to stack every one of these managers on a single scroll — fifteen stat cards
 * and fifteen forms all arguing at the same volume. Data entry is now a destination reached
 * deliberately from the ledger index at the bottom of /wealth.
 *
 * Five domains, one pattern. Assets, Investments, Gold, Insurance and Disposals each open on a
 * summary strip that answers the domain's one question, then the list. The manager is still here
 * and still does everything it did, but it now sits closed at the foot of the page: with it open by
 * default every ledger answered its question twice, once in the new shape and once in the old, and
 * the old block was the longer of the two. The pattern itself lives in
 * components/ui/LedgerSummary.tsx so the five cannot drift into five different screens.
 */
function Ledger({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <PageContainer>
      <div>
        <Link
          to="/balance"
          className="-ml-1 inline-flex min-h-11 items-center gap-1 rounded-input px-1 text-sm font-semibold text-ink-2 transition-colors hover:text-ink"
        >
          <Icon name="chevron" size={16} className="rotate-180" />
          Wealth
        </Link>
        <SectionHeader title={title} sub={sub} className="mt-1" />
      </div>
      {children}
    </PageContainer>
  )
}

/**
 * The manager, behind a disclosure. It keeps every capability it ever had — closed it is simply not
 * the page. `hint` says what is inside in the domain's own words, so opening it is a decision rather
 * than a guess.
 */
function Manager({ label = 'Add or correct', hint, children }: {
  label?: string
  hint: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  return (
    <Card padded={false} className="overflow-hidden">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-14 w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-chip bg-accent-soft text-accent">
          <Icon name="edit" size={16} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-ink">{label}</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">{hint}</span>
        </span>
        <span className="flex shrink-0 text-ink-3">
          <Icon
            name="chevron"
            size={16}
            className={`transition-transform ${open ? '-rotate-90' : 'rotate-90'}`}
          />
        </span>
      </button>
      {open && (
        <div id={panelId} className="space-y-4 border-t border-line p-4">
          {children}
        </div>
      )}
    </Card>
  )
}

const plural = (n: number, one: string, many = `${one}s`) => (n === 1 ? one : many)
const dayMonth = (iso?: string | null) => {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
/** A signed figure that must never render "+₹0" as if something happened. */
const signedShort = (n: number) => (n > 0 ? `+${shortINR(n)}` : shortINR(n))

/* ─────────────────────────────  ASSETS  ─────────────────────────────
   The question: what is it all worth, and how much of that is a price somebody would pay today
   rather than a number we worked out? `priced` answers it per row — 'live' and 'cached' are real
   market prices (a cached one is a live price captured within the last six hours), 'computed' is a
   depreciation or appreciation curve, 'manual' is a figure the household typed in. */

const TYPE_LABEL: Record<string, string> = {
  property: 'Property', stock: 'Market', gold: 'Gold', cash: 'Cash', vehicle: 'Vehicles', other: 'Other',
}
const isLivePriced = (a: Asset) => a.priced === 'live' || a.priced === 'cached'

/** One line saying what this holding IS, built only from fields the row actually carries. */
function assetQualifier(a: Asset): string {
  if (a.disposal_id) return a.note ?? 'proceeds receivable'
  const parts: string[] = []
  if (a.asset_type === 'gold' && a.quantity) {
    parts.push(`${a.quantity}g${a.meta?.purity ? ` ${a.meta.purity}` : ''}`)
  }
  if (a.asset_type === 'stock' && a.ticker) {
    parts.push(a.quantity ? `${a.quantity} × ${a.ticker}` : a.ticker)
  }
  const bought = a.meta?.purchase_date
  if (typeof bought === 'string' && bought.length >= 4) parts.push(`bought ${bought.slice(0, 4)}`)
  // For a computed value `as_of` carries the curve itself ("−14% @ 3y", "+7%/yr · 3y"); for a market
  // price it carries the timestamp, which the priced label already covers.
  if (a.priced === 'computed' && a.as_of) parts.push(a.as_of)
  if (parts.length === 0) parts.push(a.priced === 'manual' ? 'value you set' : a.priced)
  return parts.join(' · ')
}

function AssetsSummary() {
  // Same key AssetManager uses, so the strip and the manager below share one cache entry.
  const q = useQuery({ queryKey: ['networth', false], queryFn: () => fetchNetworth() })
  return (
    <LedgerBlock
      q={q}
      isEmpty={(d) => (d.assets ?? []).length === 0}
      emptyTitle="No assets recorded yet"
      emptyHint="Open “Add or correct” at the foot of this page to record the first holding, and this summary fills itself in."
    >
      {(d) => {
        const assets = d.assets ?? []
        const gross = d.total_inr ?? 0
        const live = assets.filter(isLivePriced).reduce((s, a) => s + a.value_inr, 0)
        const livePct = gross > 0 ? Math.round((live / gross) * 100) : 0

        const byType = new Map<string, Asset[]>()
        for (const a of assets) {
          const k = a.asset_type ?? 'other'
          byType.set(k, [...(byType.get(k) ?? []), a])
        }
        // Heaviest type first, and heaviest holding first inside it, so the strip opens on what
        // actually moves the number.
        const ordered = [...byType.entries()].sort(
          (x, y) =>
            y[1].reduce((s, a) => s + a.value_inr, 0) - x[1].reduce((s, a) => s + a.value_inr, 0),
        )
        const groups: LedgerGroup[] = ordered.map(([type, rows]) => ({
          key: type,
          label: TYPE_LABEL[type] ?? type,
          count: rows.length,
          rows: rows
            .slice()
            .sort((x, y) => y.value_inr - x.value_inr)
            .map<LedgerRowItem>((a) => ({
              key: a.disposal_id ? `receivable-${a.disposal_id}` : `asset-${a.id}`,
              title: a.name,
              qualifier: assetQualifier(a),
              qualifierTone: isLivePriced(a) ? 'accent' : undefined,
              value: shortINR(a.value_inr),
            })),
        }))

        return (
          <>
            <LedgerSummary
              caption={
                <>
                  <span className="money">{assets.length}</span> {plural(assets.length, 'holding')} across{' '}
                  <span className="money">{groups.length}</span> {plural(groups.length, 'type')}. Live-priced is the
                  share carrying a market price today; the rest is a figure you set or one Artha computes from a
                  purchase price.
                </>
              }
              pairs={[
                { label: 'Gross', value: shortINR(gross) },
                { label: 'Live-priced', value: `${livePct}%`, tone: 'accent' },
              ]}
            />
            <LedgerRows groups={groups} />
          </>
        )
      }}
    </LedgerBlock>
  )
}

/* ──────────────────────  RECURRING INVESTMENTS  ──────────────────────
   The question: what is going in every month, what is it worth, and which instructions are actually
   running. Two unlike things sit in this one ledger and the split says how much of the total is
   which — a fund is worth whatever the market says today, an EPF/PPF/RD/FD accrues at a stated rate
   off a balance. A fund with its standing instruction switched off is neither active nor closed: the
   money is still invested, nothing new is going in, and that is a held state, not a fault. */

/** Live market price, as opposed to contributions compounded at an assumed rate. */
const marketPriced = (r: RecurringInvestment) => Boolean(r.scheme_code && r.units && r.nav)

/** One line saying what this instruction IS — how it is valued, and whether it is still running. */
function instructionQualifier(r: RecurringInvestment): ReactNode {
  if (isFund(r)) {
    return (
      <>
        {marketPriced(r) ? 'live NAV' : 'estimated at the assumed rate'}
        {sipOff(r) && <> · SIP off, still held</>}
        {r.holder && <> · {r.holder}</>}
      </>
    )
  }
  return (
    <>
      <span className="money">{r.annual_rate_pct}</span>% accruing
      {r.matured ? <> · matured</> : r.monthly_inflow <= 0 && <> · nothing going in</>}
      {!r.from_takehome && r.monthly_inflow > 0 && <> · at source</>}
      {r.holder && <> · {r.holder}</>}
    </>
  )
}

function InvestmentsSummary() {
  // Same key and fetcher RecurringManager uses, so the strip and the manager below it share one
  // cache entry and can never disagree about the same number on the same screen.
  const q = useQuery({ queryKey: ['recurring'], queryFn: () => fetchRecurring() })
  return (
    <LedgerBlock
      q={q}
      isEmpty={(d) => (d.investments ?? []).length === 0}
      emptyTitle="No standing instructions yet"
      emptyHint="Open “Add or correct” at the foot of this page to add a SIP, RD, FD, EPF or PPF; its value, contributions and gain start accruing here."
    >
      {(d) => {
        const items = d.investments ?? []
        // The API's own answer to "is money going in", not a second derivation of it: a row's
        // monthly_inflow is zero once it is paused or matured.
        const running = items.filter((r) => r.monthly_inflow > 0).length
        const fundsValue = items.filter(isFund).reduce((s, r) => s + r.current_value, 0)
        const accruingValue = d.total_value - fundsValue
        const estimated = items.filter((r) => isFund(r) && !marketPriced(r)).length
        const giving = Math.round((d.total_value - d.total_wealth_value) * 100) / 100

        const byKind = new Map<string, RecurringInvestment[]>()
        for (const r of items) byKind.set(r.kind, [...(byKind.get(r.kind) ?? []), r])
        // Heaviest kind first, heaviest holding first inside it — the strip opens on what actually
        // moves the number, same as Assets.
        const groups: LedgerGroup[] = [...byKind.entries()]
          .sort(
            (x, y) =>
              y[1].reduce((s, r) => s + r.current_value, 0) -
              x[1].reduce((s, r) => s + r.current_value, 0),
          )
          .map(([kind, rows]) => ({
            key: kind,
            label: RECURRING_KIND_LABEL[kind] ?? kind.toUpperCase(),
            count: rows.length,
            rows: rows
              .slice()
              .sort((x, y) => y.current_value - x.current_value)
              .map<LedgerRowItem>((r) => ({
                key: `recurring-${r.id}`,
                title: r.name,
                qualifier: instructionQualifier(r),
                qualifierTone: isFund(r) && marketPriced(r) ? 'accent' : undefined,
                value: shortINR(r.current_value),
                secondary: r.monthly_inflow > 0 ? `${shortINR(r.monthly_inflow)}/mo` : '—',
              })),
          }))

        const hasNote = d.at_source_inflow > 0 || giving > 0 || estimated > 0
        return (
          <>
            <LedgerSummary
              overline="Held across every instruction"
              value={shortINR(d.total_value)}
              caption={
                <>
                  <span className="money">{items.length}</span> {plural(items.length, 'instruction')} across{' '}
                  <span className="money">{groups.length}</span> {plural(groups.length, 'kind')}. Going in is
                  everything reaching these pots each month; an instruction that is paused or matured counts
                  nothing towards it.
                </>
              }
              pairs={[
                { label: 'Going in / mo', value: shortINR(d.monthly_inflow), tone: 'accent' },
                { label: 'Running', value: `${running} of ${items.length}` },
              ]}
              split={
                fundsValue > 0 && accruingValue > 0
                  ? [
                      {
                        label: 'Market-priced funds',
                        value: shortINR(fundsValue),
                        weight: fundsValue,
                        tone: 'accent',
                      },
                      {
                        label: 'Accruing accounts',
                        value: shortINR(accruingValue),
                        weight: Math.max(0, accruingValue),
                        tone: 'ink',
                      },
                    ]
                  : undefined
              }
              note={
                hasNote ? (
                  <>
                    {d.at_source_inflow > 0 && (
                      <>
                        <span className="money">{shortINR(d.takehome_outflow)}</span>/mo of that leaves your bank
                        and <span className="money">{shortINR(d.at_source_inflow)}</span>/mo is deducted at
                        source into EPF — your share plus the employer match — so it never touches take-home.{' '}
                      </>
                    )}
                    {giving > 0 && (
                      <>
                        <span className="money">{shortINR(giving)}</span> of the total is held for committed
                        giving: a real balance, but money already spoken for, so it never becomes household
                        wealth.{' '}
                      </>
                    )}
                    {estimated > 0 && (
                      <>
                        <span className="money">{estimated}</span> {plural(estimated, 'fund')} still{' '}
                        {estimated === 1 ? 'has' : 'have'} no scheme linked, so {estimated === 1 ? 'its' : 'their'}{' '}
                        value is contributions grown at an assumed rate rather than a price anyone would pay
                        today. Link the fund and its units under “Add or correct” below to price it live.
                      </>
                    )}
                  </>
                ) : undefined
              }
            />
            <LedgerRows groups={groups} />
          </>
        )
      }}
    </LedgerBlock>
  )
}

/* ─────────────────────────────  GOLD  ─────────────────────────────
   The question: how much of it is actually free? Pledged gold is still yours and still sits in net
   worth; the loan it secures is a liability of its own. Netting one off the other here would hide
   both. */

function GoldSummary() {
  const q = useQuery({ queryKey: ['networth-summary'], queryFn: fetchNetWorthSummary })
  return (
    <LedgerBlock
      q={q}
      isEmpty={(d) => !d.gold_collateral || d.gold_collateral.real_value <= 0}
      emptyTitle="No gold recorded yet"
      emptyHint="Add it as a gold asset in the Assets ledger and it is valued at the live rate from then on."
    >
      {(d) => {
        const g = d.gold_collateral
        const loanBalance = (g.gold_loans ?? []).reduce((s, l) => s + l.balance, 0)
        const rows: LedgerRowItem[] = (g.holdings ?? []).map((h, i) => ({
          key: `${h.name}-${i}`,
          title: h.name,
          qualifier: [h.purity, h.rate_per_g ? `${shortINR(Math.round(h.rate_per_g))}/g` : null]
            .filter(Boolean)
            .join(' · ') || undefined,
          value: shortINR(h.value),
          secondary: h.grams != null ? `${h.grams} g` : undefined,
        }))
        return (
          <>
            <LedgerSummary
              overline="Holding"
              value={shortINR(g.real_value)}
              caption={
                <>
                  <span className="money">{g.total_grams}</span> g at the live rate
                  {g.priced_at ? <>, captured {dayMonth(g.priced_at)}</> : null}.
                </>
              }
              split={[
                { label: 'Pledged', value: shortINR(g.pledged_value), weight: g.pledged_value, tone: 'warn' },
                { label: 'Free', value: shortINR(g.net_value), weight: Math.max(0, g.net_value), tone: 'accent' },
              ]}
              note={
                g.pledged_value > 0 ? (
                  <>
                    Pledged gold is still yours and still counts in net worth.
                    {loanBalance > 0 && (
                      <>
                        {' '}The <span className="money">{shortINR(loanBalance)}</span> it secures is counted on the
                        liability side, not netted off here.
                      </>
                    )}
                    {g.external_pledged > 0 && (
                      <>
                        {' '}<span className="money">{shortINR(g.external_pledged)}</span> of it is pledged against a
                        loan held outside Artha.
                      </>
                    )}
                  </>
                ) : (
                  <>Nothing is pledged. The whole holding is free to sell or to borrow against.</>
                )
              }
            />
            {rows.length > 0 && <LedgerRows groups={[{ key: 'holdings', rows }]} />}
          </>
        )
      }}
    </LedgerBlock>
  )
}

/* ─────────────────────────────  INSURANCE  ─────────────────────────────
   The question: how much are you covered for, and does any of it belong on the balance sheet?
   Almost none of it does — cover pays out on an event that has not happened, so it is deliberately
   absent from net worth. Only a policy with a surrender value contributes anything. */

/** The policy anniversary — the date the cover renews — derived from the recorded start date. */
function nextAnniversary(startISO: string | null): string | null {
  if (!startISO) return null
  const start = new Date(startISO)
  if (Number.isNaN(start.getTime())) return null
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const next = new Date(today.getFullYear(), start.getMonth(), start.getDate())
  if (next < today) next.setFullYear(next.getFullYear() + 1)
  return next.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function InsuranceSummary() {
  const q = useQuery({ queryKey: ['insurance'], queryFn: fetchInsurance })
  return (
    <LedgerBlock
      q={q}
      isEmpty={(d) => (d.policies ?? []).length === 0}
      emptyTitle="No policies recorded yet"
      emptyHint="This screen shows, explains and removes policies — there is no add form for one yet."
    >
      {(d) => {
        const policies = (d.policies ?? []).filter((p) => p.active !== false)
        const rows: LedgerRowItem[] = policies
          .slice()
          .sort((x, y) => y.sum_assured - x.sum_assured)
          .map<LedgerRowItem>((p) => {
            const renews = nextAnniversary(p.start_date)
            return {
              key: `policy-${p.id}`,
              title: `${KIND_LABEL[p.kind] ?? p.kind} · ${p.provider}`,
              qualifier: (
                <>
                  <span className="money">{shortINR(p.annual_premium)}</span>/yr
                  {renews && <> · renews <span className="money">{renews}</span></>}
                </>
              ),
              value: shortINR(p.sum_assured),
            }
          })
        return (
          <>
            <LedgerSummary
              overline="Total cover"
              value={shortINR(d.total_cover)}
              caption={
                <>
                  Cover is protection, not wealth: it is deliberately not part of net worth, because it pays out on
                  an event that has not happened.
                  {d.total_surrender_value > 0 ? (
                    <>
                      {' '}The <span className="money">{shortINR(d.total_surrender_value)}</span> of surrender value
                      across these policies is real money today, and that part does count.
                    </>
                  ) : (
                    <> None of these policies carries a surrender value, so none of it lands there.</>
                  )}
                </>
              }
              pairs={[
                { label: 'Annual premium', value: shortINR(d.annual_premium) },
                { label: 'In net worth', value: shortINR(d.total_surrender_value), tone: 'accent' },
              ]}
              note={<>Renewal dates are each policy&rsquo;s anniversary, taken from the start date recorded on it.</>}
            />
            {rows.length > 0 && <LedgerRows groups={[{ key: 'policies', rows }]} />}
          </>
        )
      }}
    </LedgerBlock>
  )
}

/* ─────────────────────────────  DISPOSALS  ─────────────────────────────
   The question: what did the selling actually earn, and what does the tax side look like? A sale
   whose cost basis was never recorded has NO computable gain — it is counted out of every figure
   here rather than folded in as a zero. */

function DisposalsSummary() {
  const q = useQuery({ queryKey: ['disposals'], queryFn: fetchDisposals })
  return (
    <LedgerBlock
      q={q}
      isEmpty={(d) => (d.disposals ?? []).length === 0}
      emptyTitle="No sales recorded yet"
      emptyHint="Record a sale from the Assets ledger and it lands here with its gain and holding period."
    >
      {(d) => {
        const sales = d.disposals ?? []
        const priced = sales.filter((s) => s.gain_inr !== null)
        const gain = priced.reduce((s, x) => s + (x.gain_inr as number), 0)
        const longTerm = priced.filter((s) => s.is_long_term === true).reduce((s, x) => s + (x.gain_inr as number), 0)
        const shortTerm = priced.filter((s) => s.is_long_term === false).reduce((s, x) => s + (x.gain_inr as number), 0)
        const rows: LedgerRowItem[] = sales
          .slice()
          .sort((x, y) => (x.disposed_on < y.disposed_on ? 1 : -1))
          .map<LedgerRowItem>((s) => ({
            key: `sale-${s.id ?? `${s.asset}-${s.disposed_on}`}`,
            title: s.quantity ? `${s.asset} · ${s.quantity}` : s.asset,
            qualifier: (
              <>
                sold <span className="money">{dayMonth(s.disposed_on) ?? s.disposed_on}</span>
                {' · '}
                {s.gain_inr === null
                  ? 'cost basis not recorded'
                  : s.is_long_term === true
                    ? 'long term'
                    : s.is_long_term === false
                      ? 'short term'
                      : 'holding period unknown'}
              </>
            ),
            value: shortINR(s.gross_proceeds_inr),
            secondary: s.gain_inr === null ? undefined : signedShort(s.gain_inr),
            secondaryTone: s.gain_inr === null ? undefined : s.gain_inr < 0 ? 'negative' : 'positive',
          }))
        return (
          <>
            <LedgerSummary
              overline="Realised gain"
              value={priced.length > 0 ? signedShort(gain) : '—'}
              tone={priced.length === 0 ? 'ink' : gain < 0 ? 'negative' : 'positive'}
              caption={
                <>
                  Across <span className="money">{sales.length}</span> recorded {plural(sales.length, 'sale')}.
                  Estimated tax on the computable gains is{' '}
                  <span className="money">{shortINR(d.estimated_tax_inr)}</span>.
                </>
              }
              pairs={[
                { label: 'Long term', value: signedShort(longTerm) },
                { label: 'Short term', value: signedShort(shortTerm) },
              ]}
              note={
                d.unpriced_count > 0 ? (
                  <>
                    <span className="money">{d.unpriced_count}</span> {plural(d.unpriced_count, 'sale')} ha
                    {d.unpriced_count === 1 ? 's' : 've'} no cost basis recorded, so the gain cannot be worked out and
                    is left out of every figure above rather than counted as zero.
                  </>
                ) : undefined
              }
            />
            {rows.length > 0 && <LedgerRows groups={[{ key: 'sales', rows }]} />}
          </>
        )
      }}
    </LedgerBlock>
  )
}

/* ─────────────────────────────  THE ROUTES  ───────────────────────────── */

export function AssetsLedger() {
  return (
    <Ledger title="Assets" sub="Everything you own that carries a value — property, gold, cash, stock, vehicles.">
      <AssetsSummary />
      <Manager hint="Add a holding, change what it is worth, record a sale, or remove one.">
        <AssetManager />
      </Manager>
    </Ledger>
  )
}

export function InvestmentsLedger() {
  return (
    <Ledger title="Recurring investments" sub="Every standing instruction and accruing balance, holding by holding.">
      <InvestmentsSummary />
      <Manager hint="Add an instruction, log a top-up or a redemption, link a fund to its live NAV, pause or remove one.">
        <RecurringManager />
      </Manager>
    </Ledger>
  )
}

export function GoldLedger() {
  return (
    <Ledger title="Gold" sub="What the gold is worth, what is pledged against it, and what that leaves you.">
      <GoldSummary />
      <Manager
        label="Detail, and where to correct it"
        hint="Borrow room and the loans the pledge comes from. Grams and purity are edited in the Assets ledger."
      >
        <GoldCollateral />
        <p className="text-sm text-ink-2">
          Grams and purity are held as assets.{' '}
          <Link to="/wealth/assets" className="font-semibold text-accent hover:text-accent-2">
            Edit them in the Assets ledger
          </Link>
          .
        </p>
      </Manager>
    </Ledger>
  )
}

export function InsuranceLedger() {
  return (
    <Ledger title="Insurance" sub="Cover you hold. A policy protects the balance sheet, it is not an asset on it.">
      <InsuranceSummary />
      <Manager label="Policy detail" hint="Every field on each policy, and removing one.">
        <InsuranceManager />
      </Manager>
    </Ledger>
  )
}

export function DisposalsLedger() {
  return (
    <Ledger title="Sales & capital gains" sub="What you sold, what it cost you, and what the gain is taxed as.">
      <DisposalsSummary />
      <Manager hint="Correct a sale or its cost basis, log money received against one, or remove it.">
        <DisposalsManager />
      </Manager>
    </Ledger>
  )
}
