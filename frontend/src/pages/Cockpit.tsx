import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchDebtMoveNarration, syncAll, shortINR as inrShort, formatMoney } from '../api/client'
import { useCockpit, type Cockpit } from '../api/cockpit'
import { usePullToRefresh } from '../hooks/usePullToRefresh'
import { usePeriod } from '../hooks/usePeriod'
import { Can, useCan } from '../auth/Can'
import { StatGrid, StatCard, Card, Icon, Loading, ErrorState, PeriodToggle, PageContainer, PageGrid, CompactRow, SectionHeader, type IconName } from '../components/ui'

// Full grouped amount for the hero; compact (₹1.25 Cr) for tiles. Symbol and digit grouping follow
// the household's currency; the typographic minus is kept because Intl emits a plain hyphen.
function inrFull(n: number): string {
  return `${n < 0 ? '−' : ''}${formatMoney(Math.abs(n))}`
}

/**
 * One hero, three states, no gradient.
 *
 * This used to compute its own projection — `(spent / days_elapsed) × days_total` — which is the exact
 * formula api/budget.py rejects in its own source for scaling one-off lumps as if they recurred daily.
 * The state now comes from the server, off the owned projection. See the `Pace` type for what that
 * was worth in rupees.
 *
 * The surface no longer changes: a chip names the state and the FIGURE takes the tone, so the card
 * still reads as the same object across all three states. The old version recoloured a whole gradient
 * card to white-on-terracotta, which is outside the design system in both directions.
 */
type HeroTone = { chip: string; label: string; tone: string; bar: string; sub: string }

function heroState(d: import('../api/cockpit').Cockpit): HeroTone {
  const state = d.pace?.state ?? (d.safe_to_spend < 0 ? 'over' : 'on_track')
  const early = d.pace?.days_early
  if (state === 'over')
    return {
      chip: 'Over budget',
      // The label FLIPS with the sign. Past your free cash there is no safe amount to spend, so this
      // cannot say "safe to spend" — and the minus goes with it, because the label now carries the
      // direction. A signed negative under a positive-sounding label is the same defect as two
      // different figures sharing the word "free".
      label: 'Over by',
      tone: 'text-negative', bar: 'var(--negative)',
      sub: 'nothing left to spend — this is a hole to cover',
    }
  if (state === 'tight')
    return {
      chip: 'Getting tight', label: 'Safe to spend',
      tone: 'text-warn', bar: 'var(--warn)',
      sub: early ? `you run out ${early} ${early === 1 ? 'day' : 'days'} early at this pace`
                 : 'on pace to run out before the cycle ends',
    }
  return {
    chip: 'On track', label: 'Safe to spend',
    // The figure takes the tone so the traffic light survives without recolouring the surface.
    tone: 'text-accent', bar: 'var(--accent)',
    sub: 'room to spare at this pace',
  }
}

/**
 * Needs you — everything waiting on a human decision, in one ranked list.
 *
 * This replaces nothing visually and everything structurally. The old screen ranked five insight cards
 * at equal weight, which is no ranking at all: a narrated observation about your savings rate sat in
 * the same rail-striped card as six transactions that genuinely block the Plan's own figures. One
 * named move stays above; the things a person must actually settle collect here.
 *
 * Renders nothing when nothing is pending. The server omits empty items rather than sending zeroes,
 * because "0 transactions need a category" is a row that costs attention and then asks for nothing.
 */
function NeedsYou({ items }: { items: import('../api/cockpit').NeedsYou[] }) {
  if (!items.length) return null
  const ROWS: Record<string, { icon: IconName; tone: string }> = {
    uncategorised: { icon: 'alert', tone: 'text-warn' },
    duplicates: { icon: 'info', tone: 'text-ink-3' },
    renewal: { icon: 'shield', tone: 'text-accent' },
  }
  return (
    <div>
      <div className="tracked px-1 pb-2.5 text-[10px] font-semibold text-ink-3">Needs you</div>
      <Card padded={false} className="overflow-hidden">
        {items.map((n) => {
          const r = ROWS[n.kind]
          const title =
            n.kind === 'uncategorised' ? `${n.count} transaction${n.count === 1 ? '' : 's'} need a category`
            : n.kind === 'duplicates' ? `${n.count} possible duplicate${n.count === 1 ? '' : 's'}`
            : `Cover renews in ${n.in_days} day${n.in_days === 1 ? '' : 's'}`
          const sub =
            n.kind === 'uncategorised' ? `${inrFull(n.amount_inr)} unfiled this cycle`
            : n.kind === 'duplicates' ? 'same amount, one day apart'
            : n.policies.map((p) => `${p.provider} · ${inrFull(p.premium_inr)}`).join(' · ')
          return (
            <Link key={n.kind} to={n.to}
              className="flex min-h-[56px] items-center gap-3 border-b border-line px-4 py-2.5 transition-colors last:border-0 hover:bg-surface-2">
              <span className={`flex flex-none ${r.tone}`}><Icon name={r.icon} size={17} /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-ink">{title}</span>
                <span className="mt-0.5 block truncate text-[11px] text-ink-3">{sub}</span>
              </span>
              <Icon name="chevron" size={16} className="flex-none text-ink-3" />
            </Link>
          )
        })}
      </Card>
    </div>
  )
}

/**
 * The pace bar — spend against elapsed, and the one graphic the hero earns.
 *
 * Two facts the old hero could only tell you one of: how much is left, and whether you are getting
 * there too fast. The fill is the share of the allowance spent; the marker is the share of the cycle
 * gone. When the fill is past the marker you are ahead of your own clock, and the bar says so without
 * a number needing to be read.
 */
function PaceBar({ pace, bar }: { pace: import('../api/cockpit').Pace; bar: string }) {
  const spentPct = Math.max(0, Math.min(100, pace.spent_pct ?? 0))
  const elapsedPct = Math.max(0, Math.min(100, pace.elapsed_pct))
  // A percentage point of tolerance, because the two figures are not the same KIND of measurement:
  // spend moves in jumps when a transaction lands, elapsed moves smoothly. Live, this read
  // "19% — you're past it" off 19.5 against 19.4, which is a rounding difference presented as a
  // verdict. Under the tolerance the honest answer is that you are on pace.
  const gap = spentPct - elapsedPct
  const verdict = gap > 1 ? '— you’re past it' : gap < -1 ? '— you’re behind it' : '— you’re on it'
  return (
    <div style={{ marginTop: 14 }}>
      <div className="flex items-center justify-between text-[11px] text-ink-3">
        <span>Spent <span className="money font-semibold text-ink">{inrFull(pace.spent_inr)}</span></span>
        <span className="money">of {inrFull(pace.allowed_inr)}</span>
      </div>
      <div className="relative mt-2 h-2.5 overflow-hidden rounded-full border border-line bg-surface-2">
        <div className="absolute inset-y-0 left-0" style={{ width: `${spentPct}%`, background: bar }} />
        {/* the marker rides ON TOP of the fill — behind it, it vanishes exactly when the comparison
            it exists to make becomes true */}
        {/* Reads on BOTH sides of itself: a dark tick disappears into the fill exactly when the
            comparison it exists to make is closest, which is when it matters most. */}
        <div className="absolute inset-y-0 w-[3px] rounded-full bg-surface ring-1 ring-ink/60"
             style={{ left: `calc(${elapsedPct}% - 1.5px)` }} aria-hidden />
      </div>
      <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-ink-3">
        <span className="inline-block h-2.5 w-[3px] rounded-full bg-ink/70" aria-hidden />
        <span>
          pace marker sits at <span className="money">{Math.round(elapsedPct)}%</span> {verdict}
        </span>
      </div>
    </div>
  )
}

// route a nudge's call-to-action to the right tab
function ctaRoute(cta: string): string {
  const c = cta.toLowerCase()
  if (c.includes('payoff') || c.includes('debt')) return '/debt'
  return '/plan'
}

function ordinal(d: number): string {
  const s = ['th', 'st', 'nd', 'rd'], v = d % 100
  return d + (s[(v - 20) % 10] || s[v] || s[0])
}

// Idle sync-pill label — turns the last-sync timestamp into a trustworthy "when", so the pill always
// says something concrete instead of a bare "Sync".
function syncedLabel(ts: number): string {
  if (!ts) return 'Sync'
  const mins = Math.floor((Date.now() - ts) / 60_000)
  if (mins < 1) return 'Synced just now'
  if (mins < 60) return `Synced ${mins}m ago`
  const d = new Date(ts)
  return `Synced ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

// One "Coming up" group (Loan EMIs, SIPs…) — tap to expand into its individual commitments + due day.
function ComingUpGroup({ group }: { group: Cockpit['coming_up'][number] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b border-line last:border-0">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 py-3 text-left">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent"><Icon name="card" size={18} /></span>
        <span className="flex-1 text-sm font-medium text-ink">{group.label}
          <span className="block text-xs text-ink-3">{group.count} item{group.count !== 1 ? 's' : ''} · tap for detail</span>
        </span>
        <span className="money text-sm font-semibold text-negative">−{inrShort(group.amount)}</span>
        <span className={`text-ink-3 transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {open && (
        <div className="mb-2.5 pl-12">
          {group.items.map((it, i) => (
            <CompactRow
              key={i}
              label={it.name}
              dateChip={it.day != null ? `~${ordinal(it.day)}` : undefined}
              amount={inrShort(it.amount)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default function Cockpit() {
  const [period] = usePeriod()
  const mayEdit = useCan('ledger.import', 'edit')
  const { data, isLoading, isError, refetch } = useCockpit(period)
  // the ONE money move — same engine as the Debt tab (one advice system across the app). AI narration
  // is fetched separately so the cockpit never blocks on the model.
  const move = data?.move
  const moveNarr = useQuery({
    queryKey: ['debt-move-narration'],
    queryFn: fetchDebtMoveNarration,
    staleTime: 60_000,
    enabled: !!move && move.kind !== 'on_track' && move.kind !== 'unknown',
  })
  const qc = useQueryClient()

  // ONE-button sync: SMS + Gmail + categorize + auto-merge, then refresh every screen.
  // State machine so the pill is always honest: syncing → result (ok/failed, 6s) → idle with the
  // last-synced time. A failure stays visibly RED and clickable to retry — never a silent stall.
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const [syncErr, setSyncErr] = useState(false)
  const [lastSync, setLastSync] = useState<number>(() => Number(localStorage.getItem('artha-last-sync') || 0))
  // The permission check belongs HERE rather than only on the pill, because two of the three callers
  // are unattended: the on-open sync below and pull-to-refresh. A refused one would be invisible.
  const doSync = useCallback(async () => {
    if (syncing || !mayEdit) return
    setSyncing(true); setSyncMsg(null); setSyncErr(false)
    try {
      const r = await syncAll()
      await qc.invalidateQueries()
      const n = r.summary?.new ?? 0, merged = r.summary?.merged ?? 0
      // A partial sync must not read as a clean one. The channels have always been allowed to fail
      // on their own; the self-heal steps can now say so too, and silence would hide it.
      const failed = r.summary?.failed ?? []
      setSyncErr(failed.length > 0)
      setSyncMsg(failed.length
        ? `Synced, but ${failed.length} step${failed.length === 1 ? '' : 's'} failed — tap to retry`
        : n || merged ? `+${n} new · ${merged} merged` : 'Up to date')
      const now = Date.now()
      localStorage.setItem('artha-last-sync', String(now)); setLastSync(now)
      if (!failed.length) setTimeout(() => setSyncMsg(null), 6000)
    } catch {
      setSyncMsg('Sync failed — tap to retry'); setSyncErr(true)
      // leave the failure visible until the next attempt; don't auto-clear an error
    } finally {
      setSyncing(false)
    }
  }, [qc, syncing, mayEdit])

  // sync as soon as the app opens (near-real-time), throttled to once / 2 min so re-navigating Home
  // doesn't hammer the bank channels. The 10-min background job covers when the app is closed.
  useEffect(() => {
    const last = Number(localStorage.getItem('artha-last-sync') || 0)
    if (Date.now() - last > 120_000) doSync()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Pull down at the top → pull new transactions from the banks, then refresh every screen. Someone
  // who may not import still gets the refresh: the gesture promises fresh numbers, and returning
  // early would spin the indicator and change nothing, which reads as the app being stuck.
  const { pull, refreshing } = usePullToRefresh(
    useCallback(async () => { mayEdit ? await doSync() : await qc.invalidateQueries() },
                [mayEdit, doSync, qc]),
  )

  return (
    <PageContainer>
      {/* Today owns the one h1. Cockpit never had one because a tab shell was supplying it; when
          Commitments came off this tab the title left with it, and a screen with no h1 cannot answer
          "where am I". */}
      <SectionHeader title="Today" />

      {/* pull-to-refresh indicator + top controls — one rhythm unit so the indicator hugs the bar */}
      <div>
        <div style={{ height: pull }} className="flex items-end justify-center overflow-hidden transition-[height] duration-150">
          {pull > 0 && (
            <span className="pb-1 text-xs font-medium text-ink-3">
              {refreshing ? 'Refreshing…' : pull >= 64 ? 'Release to refresh' : 'Pull to refresh'}
            </span>
          )}
        </div>
        <div className="flex items-center justify-between gap-2">
          <PeriodToggle />
          <div className="flex items-center gap-2">
            {data?.cycle && (
              <span className="hidden text-xs font-medium text-ink-2 xs:inline">
                {data.cycle.label} · {data.cycle.days_left}d left
              </span>
            )}
            {/* The pill is 32px, per the handoff; the BUTTON around it carries the 44px hit area —
                the same split the profile disc in Layout uses. The pill was 44px, which is a tap
                target wearing a pill's clothes: it sat as tall as the period toggle beside it and
                read as a second control of equal weight rather than a status readout. */}
            {mayEdit && (
            <button onClick={doSync} disabled={syncing}
              className="flex min-h-11 items-center active:scale-95 disabled:opacity-60">
              <span className={`flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-semibold shadow-sm ${
                syncErr ? 'border-negative/40 bg-surface text-negative' : 'border-line bg-surface text-ink-2'}`}>
                <span className={syncing ? 'inline-block animate-spin' : 'inline-block'}>{syncErr ? '⚠' : '↻'}</span>
                {syncing ? 'Syncing…' : syncMsg || syncedLabel(lastSync)}
              </span>
            </button>
            )}
          </div>
        </div>
      </div>

      {isLoading && <Loading rows={4} />}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && (
        <>
          {/* Hero — safe to spend. One card, three states, no gradient: a chip names the state and the
              figure takes its tone, so the card reads as the same object whichever state it is in. */}
          {(() => {
            const hero = heroState(data)
            // Literal spec: radius 20, padding 20, hairline + the system shadow. The state lives in
            // the CHIP and the FIGURE — both take the state colour; the surface never changes.
            return (
              <Card className="!rounded-[20px] !p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  {/* 26px pill, surface-2, hairline, 10px/600 tracked .1em uppercase, TEXT in the state
                      colour — not just the dot. A grey chip with a coloured dot makes the dot the only
                      carrier of the state, at 6px. */}
                  <span
                    className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 text-[10px] font-semibold uppercase"
                    style={{ letterSpacing: '.1em', color: hero.bar }}
                  >
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: hero.bar }} />
                    {hero.chip}
                  </span>
                  <span className="text-[11px] text-ink-3">
                    <span className="money">{data.cycle.days_left}</span>d left
                  </span>
                </div>
                <div className="mt-4 text-[11px] font-semibold uppercase text-ink-3"
                     style={{ letterSpacing: '.14em' }}>{hero.label}</div>
                <div className="money font-bold"
                     style={{ fontSize: 40, lineHeight: .95, marginTop: 6, letterSpacing: '-.02em',
                              color: hero.bar }}>
                  {/* the minus is dropped in the over state — "Over by" already carries the direction */}
                  {inrFull(Math.abs(data.safe_to_spend))}
                </div>
                <p className="max-w-[34ch] text-ink-2"
                   style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.5 }}>{hero.sub}</p>
                {data.pace && data.pace.allowed_inr > 0 && (
                  <PaceBar pace={data.pace} bar={hero.bar} />
                )}
              </Card>
            )
          })()}

          {/* Glance — the 4 headline figures as one StatCard row */}
          <StatGrid min="10rem">
            <StatCard label="In" value={inrShort(data.glance.income)} tone="positive" />
            <StatCard label="Committed" value={inrShort(data.glance.committed)} />
            <StatCard label="Saved" value={inrShort(data.glance.saved)} />
            {/* NOT "Free": the hero above already says "free to spend" about safe_to_spend, a
                different number. Two figures ₹42k apart under one word is unreadable. This is
                `leftover`, which MonthlyFlow already calls "Leftover for variable spend". */}
            <StatCard label="For living" value={inrShort(data.glance.leftover)} tone="accent" />
          </StatGrid>

          {/* TWO columns, not three, because a CSS grid row is as tall as its tallest cell and the
              insight list is far taller than the other two. At three columns the short pair ran out
              and left ~600px where the left two-thirds of the screen was empty, so you scrolled a
              void to finish reading the insights. Stacking the two short ones against the tall one
              makes the sides comparable. Mobile is unaffected — it stacks to one column either way. */}
          <PageGrid cols={2}>
            <div className="space-y-5">
            {/* Coming up — real recurring commitments, tap a group to see each item + its due day */}
            <div>
              <div className="mb-2.5 flex items-baseline justify-between">
                <h2 className="text-xs font-bold uppercase tracking-[0.09em] text-ink-3">Coming up</h2>
                {data.remaining_dues != null && data.remaining_dues > 0 && (
                  <span className="text-[11px] font-medium text-ink-3"><span className="money font-semibold text-ink-2">{inrShort(data.remaining_dues)}</span> still due</span>
                )}
              </div>
              <Card padded={false} className="px-4">
                {data.coming_up.map((c) => (
                  <ComingUpGroup key={c.label} group={c} />
                ))}
                {data.coming_up.length === 0 && (
                  <div className="py-3 text-sm text-ink-3">No recurring commitments defined yet.</div>
                )}
              </Card>
              </div>
            </div>

            {/* The move, then what needs a human.

                The design's instruction, applied literally: "insights collapse from a stack of
                rail-striped cards into one named move plus a Needs you list, because five things at
                equal weight is no ranking at all." So the ranked insight cards are gone. They were
                narrated observations sharing a visual weight with items that genuinely block the
                Plan's own figures, and nothing on the screen said which was which. The /insights
                endpoint is untouched; what changed is that this screen no longer presents five
                suggestions as five equals. */}
            <div className="space-y-5">
              <div>
                <div className="mb-2.5 flex items-baseline justify-between">
                  <h2 className="tracked text-[10px] font-semibold text-ink-3">The move this cycle</h2>
                  {moveNarr.data?.ai && <span className="tracked text-[10px] text-ink-3">✦ AI</span>}
                </div>
                {move && move.kind !== 'on_track' ? (
                  <Card>
                    <p className="text-sm font-semibold leading-snug text-ink">
                      {moveNarr.data?.headline ?? move.headline}
                    </p>
                    <Link to="/balance"
                      className="mt-3 flex min-h-11 items-center justify-center rounded-xl bg-accent text-[13px] font-semibold text-white transition-colors hover:bg-accent-2">
                      See the plan
                    </Link>
                  </Card>
                ) : (
                  <Card>
                    <p className="text-sm font-semibold text-ink">Nothing needs attention</p>
                    <p className="mt-0.5 text-[13px] leading-relaxed text-ink-2">
                      You’re tracking to plan this cycle — no breaches, no pace warnings.
                    </p>
                  </Card>
                )}
              </div>

              {data.needs_you && <NeedsYou items={data.needs_you} />}

              {/* The rest of what the checks found. A link rather than a section, because the whole
                  point of collapsing the old stack was that this screen ranks ONE thing. */}
              <Can view="spending">
                <Link to="/insights"
                  className="flex min-h-[52px] items-center justify-between gap-3 rounded-2xl border border-line bg-surface px-4 transition-colors hover:border-line-2">
                  <span className="text-sm font-semibold text-ink">What to look at</span>
                  <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
                    everything else the checks found
                    <Icon name="chevron" size={15} />
                  </span>
                </Link>
              </Can>
            </div>

          </PageGrid>

          {/* Getting ahead — the long-game figures as a StatCard row */}
          <div>
            <h2 className="mb-2.5 text-xs font-bold uppercase tracking-[0.09em] text-ink-3">Getting ahead</h2>
            <StatGrid min="10rem">
              <StatCard label="Savings rate" value={`${data.ahead.savings_rate}%`} sub="of income, this month" />
              <StatCard label="Debt" value={inrShort(data.ahead.debt_total)} tone="negative" sub={`${data.ahead.loan_count} active loans`} />
              <StatCard label="Sinking funds" value={inrShort(data.ahead.sinking_total)} sub="set aside" />
              <StatCard label="Net worth" value={inrShort(data.ahead.net_worth)} tone="positive" sub="the long game" />
            </StatGrid>
          </div>
        </>
      )}
    </PageContainer>
  )
}
