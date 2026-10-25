/**
 * What to look at — the deterministic detectors, narrated.
 *
 * A destination, not a section of Today. Cockpit dropped the five-card insight stack on purpose
 * ("five things at equal weight is no ranking at all") and that call stands: Today keeps one named
 * move plus the things that genuinely block its own figures. Everything else the checks found is
 * worth reading and worth nothing at the top of a screen opened ten times a day, so it lives one tap
 * away instead.
 *
 * Follows the global lens, because a finding about "this cycle" and a finding about "this month" are
 * different findings and the endpoint takes the mode.
 */
import { useQuery } from '@tanstack/react-query'

import { fetchInsights, type Insight } from '../api/client'
import { useCan } from '../auth/Can'
import {
  Card, EmptyState, ErrorState, Icon, Loading, PageContainer, PeriodToggle, SectionHeader,
} from '../components/ui'
import { usePeriod } from '../hooks/usePeriod'

/**
 * The tone is a RANK, so it gets a word rather than only a colour. A coloured dot is unreadable to
 * anyone who cannot separate the greens from the ambers, and it is the ranking that makes this list
 * different from the stack it replaced.
 */
const TONE: Record<string, { chip: string; label: string }> = {
  negative: { chip: 'text-negative bg-negative/10', label: 'Act now' },
  warn: { chip: 'text-warn bg-warn/10', label: 'Watch' },
  accent: { chip: 'text-accent bg-accent-soft', label: 'Opportunity' },
  positive: { chip: 'text-positive bg-positive/10', label: 'Going well' },
}

function InsightCard({ item }: { item: Insight }) {
  const tone = TONE[item.tone] ?? TONE.accent
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`tracked rounded-chip px-1.5 py-0.5 text-[10px] font-semibold ${tone.chip}`}>
          {tone.label}
        </span>
        {item.category && <span className="text-[11px] text-ink-3">{item.category}</span>}
      </div>
      <p className="mt-2 text-sm font-semibold leading-snug text-ink">{item.title}</p>
      {item.body && <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{item.body}</p>}
      {item.action && (
        <p className="mt-2.5 flex items-start gap-2 border-t border-line pt-2.5 text-[12.5px] leading-relaxed text-ink">
          <span className="mt-0.5 flex-none text-accent"><Icon name="arrow" size={13} /></span>
          {item.action}
        </p>
      )}
    </Card>
  )
}

export default function Insights() {
  const [period] = usePeriod()
  const mayView = useCan('spending')
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['insights', period],
    queryFn: () => fetchInsights(period),
    enabled: mayView,
  })

  return (
    <PageContainer>
      <SectionHeader
        title="What to look at"
        sub="Everything the checks found this period, ranked. What actually blocks a figure stays on Today."
        action={<PeriodToggle />}
      />

      {!mayView && (
        <p className="rounded-input border border-line bg-surface-2 px-3 py-2 text-[11.5px] leading-relaxed text-ink-2">
          You cannot see this household&rsquo;s spending, so there is nothing to show here. Ask whoever
          administers this household if that is wrong.
        </p>
      )}

      {mayView && isLoading && <Loading rows={3} />}
      {mayView && isError && <ErrorState onRetry={() => refetch()} />}

      {data && data.insights.length === 0 && (
        <EmptyState
          icon="check"
          title="Nothing to flag"
          hint="No breaches, no pace warnings and nothing sitting idle this period."
        />
      )}

      {data && data.insights.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between px-1">
            <span className="tracked text-[10px] font-semibold text-ink-3">
              {data.insights.length} {data.insights.length === 1 ? 'finding' : 'findings'}
            </span>
            {data.source === 'ai' && <span className="tracked text-[10px] text-ink-3">✦ AI</span>}
          </div>
          {data.insights.map((item, i) => <InsightCard key={`${item.title}-${i}`} item={item} />)}
          <p className="px-1 text-[11px] leading-relaxed text-ink-3">
            Every finding is counted from your own figures. The model may reword one; it is not allowed
            to change a number, and it never invents a finding that the checks did not raise.
          </p>
        </div>
      )}
    </PageContainer>
  )
}
