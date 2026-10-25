/**
 * The Plan's four editing surfaces, off the tab.
 *
 * "No forms on any tab surface; every domain owns its own editing." Wealth already worked this way —
 * five ledgers, each opening on the question its domain answers, with the manager closed at the foot
 * of the page — while the Plan still carried income, fixed expenses, other savings and sinking funds
 * as four CRUD blocks stacked under the figures they feed. So the screen you went to in order to READ
 * whether the cycle holds was also the screen where records get typed and deleted, and that is the
 * reason there was never an obvious place to put anything new.
 *
 * Same shape as WealthLedgers on purpose. Two patterns for one job is how five screens become five
 * different screens.
 */
import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import CashflowManager from '../components/CashflowManager'
import SinkingManager from '../components/SinkingManager'
import WalletCard from '../components/WalletCard'
import { Icon, PageContainer, SectionHeader } from '../components/ui'

function PlanLedger({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <PageContainer>
      <div>
        <Link
          to="/plan"
          className="-ml-1 inline-flex min-h-11 items-center gap-1 rounded-input px-1 text-sm font-semibold text-ink-2 transition-colors hover:text-ink"
        >
          <Icon name="chevron" size={16} className="rotate-180" />
          Plan
        </Link>
        <SectionHeader title={title} sub={sub} className="mt-1" />
      </div>
      {children}
    </PageContainer>
  )
}

export function IncomeLedger() {
  return (
    <PlanLedger title="Income"
      sub="Every line that pays in. The Plan's whole allowance is built from these, so a missing one shows up as spend you appear not to be able to afford.">
      <CashflowManager kind="income" addLabel="Add income" />
    </PlanLedger>
  )
}

export function FixedLedger() {
  return (
    <PlanLedger title="Fixed expenses"
      sub="Rent, bills and anything that leaves on a schedule. These come off income before the Plan's target, which is why an unlisted one makes the target look generous.">
      <CashflowManager kind="fixed" addLabel="Add fixed expense" />
    </PlanLedger>
  )
}

export function SavingsLinesLedger() {
  return (
    <PlanLedger title="Other savings set aside"
      sub="What you commit to saving outside SIPs and sinking funds. Set aside is not spare, so it is taken out of the allowance rather than left in it.">
      <CashflowManager kind="saving" addLabel="Add line" />
    </PlanLedger>
  )
}

export function SinkingLedger() {
  return (
    <PlanLedger title="Sinking funds"
      sub="Monthly set-aside for lumpy costs — travel, repairs, premiums — so the month they land in is not the month that blows the plan.">
      <SinkingManager />
    </PlanLedger>
  )
}

/**
 * The Wallet, as a destination rather than the first thing on the Plan tab.
 *
 * It was rendered at the TOP of Plan, so the tab opened on what CLOSED cycles saved before it said
 * anything about the one you are in. The handoff lists Wallet among Plan's ledger rows, alongside
 * Income and Fixed expenses, for the same reason those are rows: a screen you consult is not a
 * screen you live on. `/wallet` did not exist as a route at all until now, which is also why the
 * placement table's own link went nowhere.
 */
export function WalletLedger() {
  return (
    <PlanLedger title="Wallet"
      sub="What closed cycles underspent, and where it has since gone. Not new money — those rupees already sit in an account net worth counts, so this is a record of them, never an addition.">
      <WalletCard />
    </PlanLedger>
  )
}
