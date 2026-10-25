import { useState } from 'react'
import Sheet from './Sheet'
import { SelectInput } from './Field'
import Icon, { type IconName } from './Icon'

/**
 * FilterPanel — the ONE filter control for the whole app (Monarch/Myntra style). A single "Filters"
 * button (with an active-count badge) opens a bottom Sheet holding every filter as a full input;
 * currently-applied filters render as removable chips inline, so the active view is always legible
 * without opening the panel. Declare filters and it renders + wires everything.
 *
 *   <FilterPanel filters={[
 *     { key: 'type', label: 'Type', value: t, onChange: setT, options: types, allLabel: 'All types' },
 *     { key: 'month', label: 'Month', type: 'month', value: m, onChange: setM },
 *   ]} />
 *
 * Use this EVERYWHERE a filter is needed — never hand-roll a filter row again.
 */
export type FilterOption = { value: string; label: string }
export type FilterDef = {
  key: string
  label: string
  value: string
  onChange: (v: string) => void
  /** 'select' (default) renders a dropdown; 'month' renders a native month picker */
  type?: 'select' | 'month'
  options?: FilterOption[]
  /** label for the empty "no filter" option, e.g. "All categories" */
  allLabel?: string
  /** custom text for the active chip (defaults to the option's label / raw value) */
  chip?: (v: string) => string
}

export function FilterPanel({
  filters,
  className = '',
  triggerLabel,
  icon,
}: {
  filters: FilterDef[]
  className?: string
  /** REQUIRED-single mode: show this text on the trigger (e.g. the current month) instead of "Filters",
   *  and drop the count badge / removable chips / clear (the value is always set). */
  triggerLabel?: string
  /** trigger icon — defaults to the funnel; pass 'calendar' etc. for single-value pickers */
  icon?: IconName
}) {
  const [open, setOpen] = useState(false)
  const single = triggerLabel != null
  const active = filters.filter((f) => f.value)
  const clearAll = () => filters.forEach((f) => f.onChange(''))
  const chipText = (f: FilterDef) =>
    f.chip ? f.chip(f.value) : f.options?.find((o) => o.value === f.value)?.label ?? f.value

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      {/* The chip is 36px, the button around it carries the 44px hit area — the same split Layout
          uses for the profile disc. Every chip in the design system is 36px (the period pill, the
          category chips on Spending); this one was 44 and so read a size larger than the chips beside
          it, which is a difference in weight the design does not intend. Splitting the two lets both
          the 36px chip and the 44px minimum be true at once. */}
      <button onClick={() => setOpen(true)} className="inline-flex min-h-11 items-center">
        <span
          className={`inline-flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-xs font-semibold transition-colors ${
            !single && active.length ? 'border-accent bg-accent-soft text-accent' : 'border-line bg-surface text-ink-2 hover:border-accent hover:text-ink'}`}
        >
          <Icon name={icon ?? 'filter'} size={15} /> {triggerLabel ?? 'Filters'}
          {!single && active.length > 0 && (
            <span className="ml-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">
              {active.length}
            </span>
          )}
        </span>
      </button>

      {!single && active.map((f) => (
        <button
          key={f.key}
          onClick={() => f.onChange('')}
          className="inline-flex min-h-11 items-center gap-1 rounded-full border border-line bg-surface px-3 text-xs font-medium text-ink-2 hover:border-negative hover:text-negative"
        >
          {chipText(f)} <span aria-hidden>✕</span>
        </button>
      ))}
      {!single && active.length > 0 && (
        <button onClick={clearAll} className="min-h-11 px-2 text-xs font-semibold text-ink-3 hover:text-ink">Clear all</button>
      )}

      <Sheet open={open} onClose={() => setOpen(false)} title={single ? (triggerLabel ?? 'Filters') : 'Filters'}>
        <div className="space-y-4">
          {filters.map((f) =>
            f.type === 'month' ? (
              <label key={f.key} className="block">
                <span className="mb-1 block text-xs font-semibold text-ink-2">{f.label}</span>
                <input
                  type="month"
                  value={f.value}
                  onChange={(e) => f.onChange(e.target.value)}
                  className="min-h-11 w-full rounded-input border border-line bg-surface px-3 text-sm text-ink outline-none focus:border-accent"
                />
              </label>
            ) : (
              <SelectInput key={f.key} label={f.label} value={f.value} onChange={(e) => f.onChange(e.target.value)}>
                <option value="">{f.allLabel ?? `All ${f.label.toLowerCase()}`}</option>
                {(f.options ?? []).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </SelectInput>
            ),
          )}
          <div className="flex gap-2 pt-1">
            {!single && (
              <button
                onClick={clearAll}
                disabled={!active.length}
                className="min-h-11 flex-1 rounded-input border border-line text-sm font-semibold text-ink-2 hover:text-ink disabled:opacity-40"
              >
                Clear all
              </button>
            )}
            <button
              onClick={() => setOpen(false)}
              className="min-h-11 flex-1 rounded-input bg-accent text-sm font-semibold text-white hover:bg-accent-2"
            >
              {single ? 'Done' : 'Show results'}
            </button>
          </div>
        </div>
      </Sheet>
    </div>
  )
}
