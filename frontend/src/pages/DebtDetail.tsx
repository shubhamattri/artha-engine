/**
 * The payoff plan and every loan — Balance's debt block, one tap down.
 *
 * Everything Debt ever had lives here: strategies, the four-method comparison, per-loan schedules,
 * prepayment logging. What it lost is the dark slab and the duplicate total, both of which Balance's
 * DebtBlock now states once.
 */
import { Link } from 'react-router-dom'

import Debt from './Debt'
import { Icon, PageContainer, SectionHeader } from '../components/ui'

export default function DebtDetail() {
  return (
    <PageContainer>
      <div>
        <Link to="/balance"
          className="-ml-1 inline-flex min-h-11 items-center gap-1 rounded-input px-1 text-sm font-semibold text-ink-2 transition-colors hover:text-ink">
          <Icon name="chevron" size={16} className="rotate-180" />
          Balance
        </Link>
        <SectionHeader title="Payoff plan" className="mt-1"
          sub="Which loan to clear first, what each one costs, and every schedule behind it." />
      </div>
      <Debt />
    </PageContainer>
  )
}
