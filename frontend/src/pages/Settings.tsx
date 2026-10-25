import AccountsManager from '../components/AccountsManager'
import HouseholdSettings from '../components/HouseholdSettings'
import Maintenance from '../components/Maintenance'
import MemberIdentity from '../components/MemberIdentity'
import SelfCheck from '../components/SelfCheck'
import { PageContainer, SectionHeader } from '../components/ui'

/**
 * Settings — where the household configures the plumbing, rather than a developer editing code.
 * Starts with accounts/cards because that is what decides whether a transaction ever gets captured.
 */
export default function Settings() {
  return (
    <PageContainer>
      <SectionHeader
        title="Settings"
        sub="Accounts, cards, and what gets tracked."
      />
      <section className="space-y-3">
        <SectionHeader
          level={2}
          title="Accounts & cards"
          sub="Every account you add here has its bank alerts captured automatically. Anything not listed is ignored."
        />
        <AccountsManager />
      </section>

      <section className="space-y-3">
        <HouseholdSettings />
      </section>

      <section className="space-y-3">
        <MemberIdentity />
      </section>

      <section className="space-y-3">
        <Maintenance />
      </section>

      {/* Last, and deliberately so: it is what you open when a figure looks wrong, not part of
          setting anything up. Until now these checks only ran over SSH before a deploy, which is
          the one place the person reading the wrong number could not see them. */}
      <section className="space-y-3">
        <SelfCheck />
      </section>
    </PageContainer>
  )
}
