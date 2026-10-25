import { useEffect, useState } from 'react'

export type Period = 'cycle' | 'month'

/** ONE global lens for the whole app — "This cycle" (per-card billing) vs "By month" (calendar).
 *  Persisted + synced across every screen via localStorage + a custom event, so toggling on Home
 *  also changes Plan, Spending and the insights. */
export function usePeriod(): [Period, (p: Period) => void] {
  const [period, setPeriod] = useState<Period>(
    () => ((localStorage.getItem('artha-period') as Period) || 'cycle'),
  )
  useEffect(() => {
    const sync = () => setPeriod((localStorage.getItem('artha-period') as Period) || 'cycle')
    window.addEventListener('artha-period', sync)
    return () => window.removeEventListener('artha-period', sync)
  }, [])
  const set = (p: Period) => {
    localStorage.setItem('artha-period', p)
    window.dispatchEvent(new Event('artha-period'))   // notify every mounted screen
  }
  return [period, set]
}
