/**
 * The two-tense switch, to the handoff's literal spec.
 *
 * Distinct from `PeriodToggle`, which is the global LENS (this cycle vs calendar month) and applies to
 * every figure in the app. This one switches TENSE on a single tab — the same numbers now versus the
 * same numbers in the past — which is why Analytics was never a separate question and why stacking
 * Plan, Analytics and Year on one page produced three heroes instead of one screen.
 */
export function Segmented<T extends string>({
  options, value, onChange,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="grid rounded-xl border border-line bg-surface-2"
         style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)`, gap: 2, padding: 3 }}>
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            aria-pressed={on}
            className={`flex items-center justify-center text-[13px] ${
              on ? 'bg-surface font-bold text-ink shadow-sm' : 'font-semibold text-ink-3'}`}
            style={{ minHeight: 38, borderRadius: 9 }}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
