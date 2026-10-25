import { useQuery } from '@tanstack/react-query'

import { fetchCorrections } from '../api/audit'
import History from '../components/History'
import { Card, PageContainer, SectionHeader, Stat, StatGrid } from '../components/ui'

/**
 * Everything anyone has changed, newest first.
 *
 * Two questions, one screen. "What happened to this number" is the log. "What do we keep
 * correcting" is the summary above it, and it is the more useful of the two: a field the household
 * fixes by hand forty times is a rule the app should have learned and did not.
 */
export default function HistoryPage() {
  const q = useQuery({ queryKey: ['audit-corrections'], queryFn: fetchCorrections })

  const byField = Object.entries(q.data?.edits_by_field ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
  const top = q.data?.top_recategorizations ?? []

  return (
    <PageContainer>
      <SectionHeader
        title="History"
        sub="Every correction anyone in this household has made, and what gets corrected most."
      />

      {byField.length > 0 && (
        <Card>
          <SectionHeader level={2} title="What gets corrected most"
            sub="Counted from hand edits only. A field near the top is one the app keeps getting wrong." />
          <StatGrid min="9rem">
            {byField.map(([field, count]) => (
              <Stat key={field} label={field} value={String(count)}
                sub={count === 1 ? 'correction' : 'corrections'} />
            ))}
          </StatGrid>
          {top.length > 0 && (
            <p className="mt-3 text-[11.5px] leading-relaxed text-ink-2">
              Most often moved into:{' '}
              {top.slice(0, 5).map((t, i) => (
                <span key={t.to}>
                  {i > 0 && ', '}
                  <span className="font-medium text-ink">{t.to}</span> ({t.count})
                </span>
              ))}
            </p>
          )}
        </Card>
      )}

      <Card>
        <SectionHeader level={2} title="Recent changes" />
        <History limit={100} />
      </Card>
    </PageContainer>
  )
}
