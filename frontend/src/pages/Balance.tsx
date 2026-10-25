/**
 * TAB 4 · Balance — the standing position. Net worth IS assets minus liabilities, and the app was
 * computing the answer on one tab while hiding half the input on another.
 *
 * This was Wealth's whole page plus Debt's whole page pasted under a new hero: net worth rendered at
 * 44px and again at 36px, the debt total five times, and Debt's dark #231F1C slab still there under a
 * white summary of itself. That is what "merge" must not mean.
 *
 * One hero carrying both sides. Composition. Debt as a peer BLOCK — the design is explicit that
 * everything it had "survives one tap down", so strategies, per-loan schedules and prepayment logging
 * live at /balance/debt rather than inline. Then tools, then the ledgers.
 */
import { Link } from 'react-router-dom'

import BalanceHero, { DebtBlock } from '../components/BalanceHero'
import Savings from './Savings'
import { Icon, LedgerList, LedgerRow, PageContainer, SectionHeader } from '../components/ui'

export default function Balance() {
  return (
    <PageContainer>
      <SectionHeader
        title="Balance"
        sub="What you own and what you owe against it — the two halves of one net-worth figure."
      />

      <BalanceHero />
      <DebtBlock />

      <LedgerList>
        <LedgerRow to="/balance/debt" label="Payoff plan & every loan" first />
        {/* Invoked, not inhabited: a lump sum landing is the moment the own/owe question becomes a
            decision, and it is a twice-a-year event rather than a place to live. */}
        <LedgerRow to="/windfall" label="Windfall" sub="a lump sum: prepay or invest" />
      </LedgerList>

      <Savings />
    </PageContainer>
  )
}
