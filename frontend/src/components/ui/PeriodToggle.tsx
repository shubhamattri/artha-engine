import { usePeriod } from '../../hooks/usePeriod'

/** The global "This cycle | By month" switch. Drop it on any screen — they all share the lens. */
export default function PeriodToggle() {
  const [period, setPeriod] = usePeriod()
  return (
    <div className="inline-flex rounded-xl border border-line p-0.5">
      {(['cycle', 'month'] as const).map((m) => (
        <button key={m} onClick={() => setPeriod(m)}
          className={`inline-flex min-h-11 items-center rounded-lg px-3 text-xs font-semibold ${period === m ? 'bg-accent text-white' : 'text-ink-2'}`}>
          {m === 'cycle' ? 'This cycle' : 'By month'}
        </button>
      ))}
    </div>
  )
}
