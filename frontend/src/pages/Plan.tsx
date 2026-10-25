/**
 * TAB 3 · Plan — targets in two tenses.
 *
 * This was three pages stacked: Budget, then Analytics, then Year, each with its own hero and its own
 * page header. Eight h2s and three competing headline figures on one tab, which is not a merge, it is
 * a concatenation.
 *
 * The design's answer is a SWITCH, not a stack: "Analytics was never a separate question — it is this
 * cycle in the past tense." So one tab, one hero at a time, and the tense chooses which.
 *
 * Year sits behind a band inside History rather than inline, because it answers a yearly question and
 * pasting it here put a third headline under the second one.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'

import Analytics from './Analytics'
import Budget from './Budget'
import { fetchYear, shortINR } from '../api/client'
import { Icon, PageContainer, SectionHeader, Segmented } from '../components/ui'
import { useQuery } from '@tanstack/react-query'

/** The door to the Year screen. Silent on failure — a doorway must never be why Plan shows an error. */
function YearBand() {
  const q = useQuery({ queryKey: ['year', 'band'], queryFn: () => fetchYear() })
  const y = q.data
  if (!y || !y.envelope_inr) return null
  const over = y.net_spent_inr > y.envelope_inr
  return (
    <Link to="/year"
      className="flex items-center gap-3 rounded-2xl border border-line bg-surface transition-colors hover:border-line-2"
      style={{ minHeight: 52, padding: '8px 16px' }}>
      <span className="min-w-0 flex-1">
        <span className="tracked block text-[10px] text-ink-3">The year · {y.label}</span>
        <span className="mt-0.5 block truncate text-sm text-ink">
          <span className={`money font-bold ${over ? 'text-negative' : 'text-ink'}`}>{shortINR(y.net_spent_inr)}</span>{' '}
          <span className="text-ink-2">of</span>{' '}
          <span className="money font-semibold">{shortINR(y.envelope_inr)}</span>{' '}
          <span className="text-ink-2">{y.exhausted ? '· budget gone' : '· still inside'}</span>
        </span>
      </span>
      <Icon name="chevron" size={16} className="shrink-0 text-ink-3" />
    </Link>
  )
}

export default function Plan() {
  const [tense, setTense] = useState<'now' | 'history'>('now')
  return (
    <PageContainer>
      <SectionHeader
        title="Plan"
        sub={tense === 'now'
          ? 'What this period allows, and where it is going.'
          : 'The same numbers in the past tense — where your targets should sit.'}
      />

      <Segmented
        value={tense}
        onChange={setTense}
        options={[{ value: 'now', label: 'This cycle' }, { value: 'history', label: 'History' }]}
      />

      {tense === 'now' ? <Budget /> : <><Analytics /><YearBand /></>}
    </PageContainer>
  )
}
