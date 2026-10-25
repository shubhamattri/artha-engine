import { useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import AccountsSection from '../components/AccountsSection'
import UploadStatements from '../components/UploadStatements'
import EditTxnSheet from '../components/EditTxnSheet'
import DuplicatesReview from '../components/DuplicatesReview'
import DuplicateReview from '../components/DuplicateReview'
import CategoryReview from '../components/CategoryReview'
import {
  fetchTransactions,
  fetchTransactionFacets,
  fetchCategories,
  currentMonth,
  formatINR,
  type Transaction,
} from '../api/client'
import {
  PageContainer,
  SectionHeader,
  Card,
  TxnRow,
  Chip,
  PeriodToggle,
  FilterPanel,
} from '../components/ui'
import { usePeriod } from '../hooks/usePeriod'
import { useHouseholdCycle } from '../api/meta'

/** Describes THIS household's billing cycle from config — never hardcodes bank names or dates.
 *  One unified window is the default; if the household bills each card on its own statement day
 *  (per_card_cycles) it names those days from config instead. */
function CycleNote() {
  const { data: cyc } = useHouseholdCycle()
  if (!cyc) return null
  const ord = (d: number) => {
    const s = ['th', 'st', 'nd', 'rd'][(d % 100 - 20) % 10] ?? ['th', 'st', 'nd', 'rd'][d % 100] ?? 'th'
    return `${d}${s}`
  }
  const days = Object.values(cyc.card_cycle_days ?? {})
  const uniq = [...new Set(days)].sort((a, b) => a - b)
  return (
    <p className="text-xs text-ink-3">
      {cyc.per_card_cycles && uniq.length > 0
        ? `Each card's own billing cycle — statements start on the ${uniq.map(ord).join(' and ')}. Matches Home & Plan.`
        : `One billing cycle for every card and account — the ${ord(cyc.cycle_start_day)} to the ${ord(cyc.cycle_start_day)} of the next month. Matches Home & Plan.`}
    </p>
  )
}

const PAGE_SIZE = 50

// Mirrors the tailwind cat-1…cat-8 data palette. Categories rotate through these by a stable
// hash of the category value — never keyed on a hardcoded category name (red stays reserved).
// The palette lives in lib/strap.ts, once. Three files each carried a private copy of this array and
// all three still had #7A5C82 in it — the plum the design drops entirely — so the ledger was painting
// categories in a colour that is not in the system. A copied constant cannot be corrected.
import { STRAP_PALETTE } from '../lib/strap'
const CAT_COLORS = STRAP_PALETTE
function catColor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return CAT_COLORS[h % CAT_COLORS.length]
}

/** Make raw bank descriptions readable: drop channel prefixes, ref numbers, UPI handles. Safe fallback. */
/** "Monday 18 Aug" — the weekday is what you actually navigate by when scanning a cycle. */
function dayLabel(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.toLocaleDateString('en-IN', { weekday: 'long' })} ${d.getDate()} ${d.toLocaleDateString('en-IN', { month: 'short' })}`
}

function prettyMerchant(raw: string): string {
  let s = raw
    .replace(/^(UPI[-/]|ACH [CD]-|BIL\/|NEFT (CR|DR)-|IB FUNDS TRANSFER (CR|DR)-|MMT\/IMPS\/\d+\/|FT ?-?|POS\s)/i, '')
    .replace(/[-@/][A-Za-z0-9.]*\d{4,}[A-Za-z0-9.@]*/g, ' ') // ref/handle blobs with long digit runs
    .replace(/\b\d{6,}\b/g, ' ')                              // long bare numbers
    .replace(/\b(PVT ?LTD|LIMITED|PAYTM|GPAY|RUPAY|UPI)\b/gi, ' ')
    .replace(/\(\s*ref#?\s*\)/gi, '')          // empty "(Ref# )" left after stripping numbers
    .replace(/\bref#?\b/gi, '')
    .replace(/[*_]+/g, ' ')
    .replace(/[\s,(–-]+$/g, '')                 // trailing punctuation/commas/open-parens
    .replace(/\s{2,}/g, ' ')
    .trim()
  if (s.length < 3) return raw.slice(0, 38)
  if (s === s.toUpperCase()) s = s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
  return s.length > 40 ? s.slice(0, 40) + '…' : s
}

function shiftMonth(m: string, delta: number): string {
  const [y, mo] = m.split('-').map(Number)
  const d = new Date(y, mo - 1 + delta, 1)
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
}
function monthLabel(m: string): string {
  const [y, mo] = m.split('-').map(Number)
  return new Date(y, mo - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' })
}

export default function Transactions() {
  const [selectedMonth, setSelectedMonth] = useState<string>(currentMonth())
  const [selectedCategory, setSelectedCategory] = useState<string>('')
  const [selectedType, setSelectedType] = useState<string>('')
  const [selectedCard, setSelectedCard] = useState<string>('')  // card_last4
  const [mode] = usePeriod()                                    // shared global lens
  const [offset, setOffset] = useState<number>(0)
  const [editing, setEditing] = useState<Transaction | null>(null)

  const facets = useQuery({ queryKey: ['txn-facets'], queryFn: fetchTransactionFacets })
  const categories = useQuery({ queryKey: ['categories'], queryFn: fetchCategories })

  const txnQuery = useQuery({
    queryKey: ['txns', mode, selectedMonth, selectedCategory, selectedType, selectedCard, offset],
    queryFn: () =>
      fetchTransactions({
        // cycle lens: the live cycle when on the current month, else that month's cycle (step ‹ ›).
        cycle: mode === 'cycle' ? (selectedMonth === currentMonth() ? 'current' : selectedMonth) : undefined,
        month: mode === 'month' ? (selectedMonth || undefined) : undefined,
        category: selectedCategory || undefined,
        txn_type: selectedType || undefined,
        card_last4: selectedCard || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: keepPreviousData,
  })

  const transactions = txnQuery.data?.transactions ?? []
  const total = txnQuery.data?.total ?? 0
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE_SIZE, total)
  const hasPrev = offset > 0
  const hasNext = offset + PAGE_SIZE < total

  return (
    <PageContainer>
      <SectionHeader
        title="Spending"
        sub="Tap any row to fix its category, amount or account."
        action={
          <span className="money text-xs text-ink-3">
            {total.toLocaleString('en-IN')} transactions{txnQuery.isFetching && !txnQuery.isLoading ? ' · updating' : ''}
          </span>
        }
      />

      {/* shared global lens — same toggle as Home & Plan. In cycle mode a ‹ › stepper walks previous cycles. */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          {mode === 'cycle' ? (
            <div className="flex items-center gap-1 text-ink">
              <button onClick={() => { setSelectedMonth((m) => shiftMonth(m, -1)); setOffset(0) }} className="inline-flex min-h-11 min-w-11 items-center justify-center text-lg text-ink-2 hover:text-ink" aria-label="Previous cycle">‹</button>
              <span className="rounded-chip bg-surface-2 px-2.5 py-1 text-xs font-semibold text-ink">{monthLabel(selectedMonth)} cycle</span>
              <button onClick={() => { setSelectedMonth((m) => shiftMonth(m, 1)); setOffset(0) }} disabled={selectedMonth === currentMonth()} className="inline-flex min-h-11 min-w-11 items-center justify-center text-lg text-ink-2 hover:text-ink disabled:opacity-30" aria-label="Next cycle">›</button>
            </div>
          ) : <span />}
          <PeriodToggle />
        </div>
        {mode === 'cycle' && <CycleNote />}
      </div>

      {/* filters — the shared app-wide FilterPanel (one button → panel + removable active chips). */}
      <FilterPanel
        filters={[
          ...(mode === 'month'
            ? [{ key: 'month', label: 'Month', type: 'month' as const, value: selectedMonth,
                 onChange: (v: string) => { setSelectedMonth(v); setOffset(0) } }]
            : []),
          { key: 'type', label: 'Type', value: selectedType, allLabel: 'All types',
            onChange: (v: string) => { setSelectedType(v); setOffset(0) },
            options: (facets.data?.types ?? []).map((t) => ({ value: t, label: t })) },
          { key: 'category', label: 'Category', value: selectedCategory, allLabel: 'All categories',
            onChange: (v: string) => { setSelectedCategory(v); setOffset(0) },
            options: (categories.data ?? []).map((c) => ({ value: c.value, label: c.value })) },
          { key: 'card', label: 'Card', value: selectedCard, allLabel: 'All cards',
            onChange: (v: string) => { setSelectedCard(v); setOffset(0) },
            options: (facets.data?.cards ?? []).map((c) => ({ value: c.card_last4, label: c.label })),
            chip: (v: string) => (facets.data?.cards ?? []).find((c) => c.card_last4 === v)?.label ?? `••${v}` },
        ]}
      />

      {/* txn table → compact rows; 2-col on desktop so the width is used, amount never truncated */}
      {txnQuery.isLoading ? (
        <Card><p className="py-12 text-center text-sm text-ink-3">Loading…</p></Card>
      ) : txnQuery.isError ? (
        <Card><p className="py-12 text-center text-sm text-negative">Failed to load transactions</p></Card>
      ) : transactions.length === 0 ? (
        <Card><p className="py-12 text-center text-sm text-ink-3">No transactions for this filter</p></Card>
      ) : (
        /* Grouped by DAY. A flat list of sixty rows makes you read every date to find the one you
           want; a day eyebrow lets you skip to it. Rows are 60px with the category as a 3px pill
           INSIDE the row — never a coloured card border, which reads as a state rather than a
           taxonomy. */
        <div className="space-y-4">
          {Object.entries(
            transactions.reduce((acc: Record<string, Transaction[]>, t: Transaction) => {
              const d = (t.date || '').slice(0, 10)
              ;(acc[d] ||= []).push(t)
              return acc
            }, {}),
          ).map(([day, rows]) => (
            <div key={day}>
              <div className="tracked px-1 pb-2.5 text-[10px] font-semibold text-ink-3">
                {dayLabel(day)}
              </div>
              <Card padded={false} className="overflow-hidden">
                {(rows as Transaction[]).map((t) => {
                  const card = t.card_last4 && t.card_last4 !== '0000' ? ` ••${t.card_last4}` : ''
                  return (
                    <TxnRow
                      key={t.id}
                      description={t.merchant ?? prettyMerchant(t.description)}
                      meta={<>{[t.category, t.bank].filter(Boolean).join(' · ')}{card}</>}
                      amount={`${t.direction === 'credit' ? '+' : '−'}${formatINR(t.amount)}`}
                      tone={t.direction === 'credit' ? 'positive' : 'default'}
                      accent={t.category ? catColor(t.category) : 'var(--line-2)'}
                      onClick={() => setEditing(t)}
                    />
                  )
                })}
              </Card>
            </div>
          ))}
        </div>
      )}

      {/* pagination */}
      <div className="flex items-center justify-between text-sm text-ink-3">
        <span className="money text-xs text-ink-3">
          {total === 0 ? 'No results' : `${pageStart}–${pageEnd} of ${total.toLocaleString('en-IN')}`}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={!hasPrev}
            className="min-h-11 rounded-input border border-line px-4 text-sm font-semibold text-ink hover:border-line-2 disabled:opacity-30"
          >
            Prev
          </button>
          <button
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={!hasNext}
            className="min-h-11 rounded-input border border-line px-4 text-sm font-semibold text-ink hover:border-line-2 disabled:opacity-30"
          >
            Next
          </button>
        </div>
      </div>

      {/* Housekeeping goes BELOW the spending, not above it.
          A fix-queue, an accounts warning and an upload dropzone used to own the entire first screen
          of a tab called Spending, so the thing the tab's name promises was under the fold. These are
          real and worth doing, but they are chores about the data, not the answer you opened the page
          for. The list leads; the maintenance follows, under a heading that says what it is. */}
      <div className="pt-2">
        <SectionHeader level={2} title="Housekeeping"
          sub="Tidying that makes the numbers above more accurate." />
        <div className="mt-3 space-y-4">
          <DuplicatesReview />
          <DuplicateReview />
          <CategoryReview />
          <AccountsSection />
          <UploadStatements />
        </div>
      </div>

      <EditTxnSheet txn={editing} onClose={() => setEditing(null)} />
    </PageContainer>
  )
}
