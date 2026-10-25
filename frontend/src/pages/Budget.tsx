/**
 * The Plan's reading surface — "This cycle" inside the Plan tab.
 *
 * It used to carry four CRUD managers (income, fixed expenses, sinking funds, other savings) stacked
 * under the figures they feed, so the screen you opened to READ whether the cycle holds was also the
 * screen where records get typed and deleted. Per the redesign's third rule — no forms on any tab
 * surface, every domain owns its own editing — they are now four destinations, reached from the row at
 * the foot of this section. Nothing was removed; each manager kept every capability it had.
 */
import PlanCategories from '../components/PlanCategories'
import IncomeHealth from '../components/IncomeHealth'
import { LedgerList, LedgerRow, PageContainer, SectionHeader } from '../components/ui'

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <SectionHeader level={2} title={title} sub={hint} />
      {children}
    </section>
  )
}

/** What the allowance is built from. Reading the Plan never requires opening one of these. */
const LEDGERS = [
  { to: '/plan/income', label: 'Income' },
  { to: '/plan/fixed', label: 'Fixed expenses' },
  { to: '/plan/sinking', label: 'Sinking funds' },
  { to: '/plan/savings', label: 'Other savings set aside' },
  // The handoff lists Wallet here, with the other ledgers. It used to render as a full card at the
  // top of this tab, so Plan opened on what CLOSED cycles saved before saying anything about the
  // cycle you are in.
  { to: '/wallet', label: 'Wallet' },
]

export default function Budget() {
  return (
    <PageContainer>
      {/* No header here: the tab's segmented control already says "This cycle", and repeating it as
          a section heading directly beneath made the tense look like a subsection of itself. */}
      {/* hero: full-width spending picture (its own 2-col category grid inside) */}
      <Section title="Spending by category"><PlanCategories /></Section>

      <div>
        <div className="tracked px-1 pb-2.5 text-[10px] font-semibold text-ink-3">
          What the allowance is built from
        </div>
        <LedgerList>
          {LEDGERS.map((l, i) => (
            <LedgerRow key={l.to} to={l.to} label={l.label} first={i === 0} />
          ))}
        </LedgerList>
      </div>

      {/* Where the allowance comes from, in one bar. Moved off Today, whose job is one number, onto
          the tab that owns the target this splits into. */}
      <IncomeHealth />
    </PageContainer>
  )
}
