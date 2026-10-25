import { useQuery } from '@tanstack/react-query'
import { fetchNetWorthSummary, shortINR as inrShort } from '../api/client'
import { Card, SectionHeader, PageGrid, Money, Loading, ErrorState } from './ui'

// Category swatches rotate through the frozen cat-1…cat-8 palette BY INDEX — never keyed on a
// category name, never red (red is reserved for negative / over-budget only).
const CAT_BG = ['bg-cat-1', 'bg-cat-2', 'bg-cat-3', 'bg-cat-4', 'bg-cat-5', 'bg-cat-6', 'bg-cat-7', 'bg-cat-8']
const catClass = (i: number) => CAT_BG[i % CAT_BG.length]

export default function NetWorthSummary() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['networth-summary'], queryFn: fetchNetWorthSummary,
  })
  if (isLoading) return <Loading rows={3} />
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />

  const gross = Math.max(data.total_assets, 1)
  return (
    <div className="space-y-6">
      {/* One statement, and it absorbs the two tiles that used to sit beneath it.
          This was a white card with a bigger number followed by two MORE white cards (Assets, Owe) —
          the same composition three times on one screen, which is what makes every page here look
          alike. Assets and Owe are what net worth is made OF, so they belong inside it on a hairline
          row rather than beside it as peers.
          Flat deep green, deliberately not the dashboard's gradient: a gradient suits the live number
          you spend against, a standing balance should sit still. Debt's hero is near-black weight;
          this is the same shape in the opposite register. */}
      <div className="overflow-hidden rounded-3xl" style={{ background: '#0B5E3F' }}>
        <div className="px-6 pb-6 pt-7">
          <div className="tracked text-[11px] font-semibold" style={{ color: '#8FC7AC' }}>Net worth</div>
          <div className="money mt-1 font-bold leading-[0.95] text-white"
            style={{ fontSize: 'clamp(2.5rem,11vw,3.5rem)' }}>{inrShort(data.net_worth)}</div>
          <div className="mt-2 text-[13px]" style={{ color: '#B7D9C8' }}>what you own, minus what you owe</div>
        </div>
        <div className="flex" style={{ borderTop: '1px solid #17724F' }}>
          <div className="flex-1 px-6 py-3">
            <div className="tracked text-[10px] font-semibold" style={{ color: '#7FB79C' }}>Assets</div>
            <div className="money mt-0.5 text-base font-semibold text-white">{inrShort(data.total_assets)}</div>
          </div>
          <div className="flex-1 px-6 py-3" style={{ borderLeft: '1px solid #17724F' }}>
            <div className="tracked text-[10px] font-semibold" style={{ color: '#7FB79C' }}>Owe</div>
            <div className="money mt-0.5 text-base font-semibold" style={{ color: '#F4B8AB' }}>{inrShort(data.total_liabilities)}</div>
          </div>
        </div>
      </div>

      {/* the allocation now gets the whole band instead of sharing it with two tiles */}
      <PageGrid cols={1} className="items-start">
        <Card>
          <SectionHeader level={2} title="Where it sits" />
          <div className="mt-3">
            {data.categories.map((c, i) => (
              <div key={c.category} className="border-b border-line py-3 last:border-0">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2.5 text-sm font-medium text-ink">
                    <span className={`h-2.5 w-2.5 flex-none rounded-chip ${catClass(i)}`} />
                    <span className="truncate">{c.category}</span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold text-ink"><Money value={c.value} /></span>
                </div>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-chip bg-bg">
                  <div
                    className={`h-full rounded-chip ${catClass(i)}`}
                    style={{ width: `${Math.min(100, (c.value / gross) * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </PageGrid>
    </div>
  )
}
