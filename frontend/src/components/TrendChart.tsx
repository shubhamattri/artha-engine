import {
  Bar,
  BarChart,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { MonthTrend } from '../api/client'
import { formatINR, shortINR } from '../api/client'

/**
 * In vs out, month by month — and deliberately NOT a second spend chart.
 *
 * This used to plot `total_spend` alone, which is the figure the History screen already draws in
 * "Spend by month" — off a different slice. That one is variable_spend (the everyday categories,
 * net of refunds); this endpoint counts every SPEND row, rent and insurance included. Two bars of
 * "monthly spend" that disagree by the rent is the exact failure this codebase has been burned by,
 * so the spend series only earns its place next to the income series it can be read against, and
 * the caller is required to label the slice.
 *
 * `total_credit` is not drawn. It is gross inflow — card-bill credits and inter-account transfers
 * included — so a chart of it says the household earned its own transfers.
 */
const IN = '#0F7A52'    // accent green — money arriving
const OUT = '#A6704A'   // clay, from the frozen palette; NOT the gold "Spend by month" uses for its
                        // peak, so the two charts cannot be mistaken for the same series

// Sage light-theme tokens (mirror index.css :root) for the chart chrome.
const INK_3 = '#8E9384'
const LINE = '#E5E0D5'
const SURFACE = '#FFFFFF'
const INK = '#14150E'
const SURFACE_2 = '#FAF8F2'
const MONO = '"JetBrains Mono", ui-monospace, monospace'

function label(m: string): string {
  const [y, mo] = m.split('-')
  const names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${names[Number(mo)] ?? mo} '${(y ?? '').slice(2)}`
}

export default function TrendChart({ months }: { months: MonthTrend[] }) {
  if (!months?.length) {
    return (
      <div className="flex h-[300px] items-center justify-center text-sm text-ink-3">
        No months with data yet
      </div>
    )
  }

  const data = months.map((m) => ({ ...m, label: label(m.month) }))

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 16, right: 8, left: 0, bottom: 0 }} barGap={2}>
        <XAxis
          dataKey="label"
          tick={{ fontSize: 11, fill: INK_3 }}
          axisLine={{ stroke: LINE }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={(v: number) => shortINR(v)}
          tick={{ fontSize: 11, fill: INK_3, fontFamily: MONO }}
          axisLine={false}
          tickLine={false}
          width={52}
        />
        <Tooltip
          cursor={{ fill: SURFACE_2 }}
          contentStyle={{
            background: SURFACE,
            border: `1px solid ${LINE}`,
            borderRadius: 12,
            fontSize: 12,
            boxShadow: '0 8px 24px -12px rgba(20,21,14,0.25)',
          }}
          itemStyle={{ color: INK, fontFamily: MONO }}
          labelStyle={{ color: INK_3 }}
          formatter={(value: number, name: string) => [formatINR(value), name]}
        />
        <Legend
          verticalAlign="top"
          height={28}
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 11, color: INK_3 }}
        />
        <Bar dataKey="total_income" name="In" fill={IN} radius={[6, 6, 0, 0]} maxBarSize={26} />
        <Bar dataKey="total_spend" name="Out" fill={OUT} radius={[6, 6, 0, 0]} maxBarSize={26} />
      </BarChart>
    </ResponsiveContainer>
  )
}
