import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  fetchCategoryConsistency, fetchCategories, setMerchantCategory, formatINR,
  type ConsistencyMerchant,
} from '../api/client'
import { Card, SectionHeader, SelectInput } from './ui'
import { ViewOnly, useCan } from '../auth/Can'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'

/** Review queue: merchants whose spend is split across categories. Pick the right one once and every
 *  transaction for that merchant is fixed + remembered (a user rule), so it stays consistent. */
export default function CategoryReview() {
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('ledger.housekeeping', 'edit')
  const { data } = useQuery({ queryKey: ['category-consistency'], queryFn: fetchCategoryConsistency })
  const cats = useQuery({ queryKey: ['categories'], queryFn: fetchCategories })
  const merchants = data?.merchants ?? []
  if (merchants.length === 0) return null

  return (
    <Card>
      <SectionHeader level={2} title={`Fix categories · ${merchants.length}`}
        sub="These merchants land in more than one category. Pick the right one — it fixes every transaction for that merchant and remembers it." />
      {/* Said once at the top rather than on each of up to twelve rows. */}
      {!mayEdit && <div className="mt-3"><ViewOnly what="the merchants whose categories disagree" /></div>}
      <div className="mt-3 space-y-2">
        {merchants.slice(0, 12).map((m) => (
          <MerchantRow key={m.key} m={m}
            options={(cats.data ?? []).filter((c) => c.is_spend).map((c) => c.value)}
            onFixed={refresh} />
        ))}
      </div>
    </Card>
  )
}

function MerchantRow({ m, options, onFixed }: {
  m: ConsistencyMerchant; options: string[]; onFixed: () => void
}) {
  const mayEdit = useCan('ledger.housekeeping', 'edit')
  const [pick, setPick] = useState(m.categories[0]?.category ?? '')
  const fix = useMutation({
    mutationFn: () => setMerchantCategory(m.key, pick),
    onSuccess: () => onFixed(),
  })
  return (
    <div className="rounded-2xl border border-line bg-surface p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-ink">{m.display}</div>
          <div className="mt-0.5 text-[11px] text-ink-3">
            {m.categories.map((c) => `${c.category} (${c.count}·${formatINR(c.amount)})`).join('  ·  ')}
          </div>
        </div>
        <span className="money shrink-0 text-xs font-bold text-ink">{formatINR(m.total)}</span>
      </div>
      {/* The picker goes with the button: on its own it is a control that decides nothing. */}
      {mayEdit && (
      <div className="mt-2 flex items-center gap-2">
        <SelectInput label="" value={pick} onChange={(e) => setPick(e.target.value)}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </SelectInput>
        <button onClick={() => fix.mutate()} disabled={fix.isPending || !pick}
          className="inline-flex min-h-11 shrink-0 items-center rounded-input bg-accent px-3 text-xs font-semibold text-surface disabled:opacity-50">
          {fix.isPending ? 'Fixing…' : 'Fix all'}
        </button>
      </div>
      )}
    </div>
  )
}
