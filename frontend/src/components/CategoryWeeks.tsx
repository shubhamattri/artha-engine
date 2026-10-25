import { useQuery } from '@tanstack/react-query'
import { fetchCategoryWeeks, type CategoryMatrix } from '../api/client'
import AnalyticsMatrix from './AnalyticsMatrix'

/** Category × week for the selected month — "how much on groceries in week 1?" Renders through the SAME
 *  matrix as Analytics (heat-tint, week-over-week ▲▼, Avg, id-drill), so the format is consistent. */
export default function CategoryWeeks({ anchor, mode }: { anchor: string; mode: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['category-weeks', anchor, mode],
    queryFn: () => fetchCategoryWeeks(anchor, mode),
  })
  if (isLoading || !data || data.weeks.length === 0 || data.categories.length === 0) return null

  // Shape the week grid like the analytics matrix: columns = weeks, cells carry their txn ids.
  const view: CategoryMatrix = {
    months: data.weeks.map((w) => `w${w.week}`),
    grand_total_by_month: {},
    categories: data.categories.map((c) => {
      const byMonth: Record<string, number> = {}
      const idsByMonth: Record<string, number[]> = {}
      data.weeks.forEach((w, i) => {
        byMonth[`w${w.week}`] = c.weeks[i] ?? 0
        idsByMonth[`w${w.week}`] = c.txn_ids[i] ?? []
      })
      const nz = c.weeks.filter((v) => v > 0)
      return {
        category: c.category, byMonth, idsByMonth, total: c.total,
        avg: nz.length ? Math.round((c.total / nz.length) * 100) / 100 : 0,
      }
    }),
  }
  return <AnalyticsMatrix data={view} labelFor={(k) => k.toUpperCase()} colHint="Week × category" />
}
