import type { RecurringInvestment } from '../api/client'

/**
 * The fund selectors, in ONE place. The Wealth tab's summary card and the /wealth/funds screens
 * both answer "what are the funds worth" — if each derived it locally they would eventually
 * disagree on the same screen.
 */

/** Market-linked holdings only. An EPF or a PPF accrues at a stated rate; it is not a fund. */
export const isFund = (r: RecurringInvestment) => r.kind === 'sip'

/** Held, with the standing instruction stopped. Neither active nor closed — a real third state. */
export const sipOff = (f: RecurringInvestment) => !f.active || f.monthly_contribution <= 0

/** Worth less than what went in. Cost-basis, so it holds even when the API reports no return_pct. */
export const isDown = (f: RecurringInvestment) => f.current_value < f.total_contributed

export function fundStats(investments: RecurringInvestment[]) {
  const funds = investments.filter(isFund)
  const value = funds.reduce((s, f) => s + f.current_value, 0)
  const contributed = funds.reduce((s, f) => s + f.total_contributed, 0)
  const sip = funds.filter((f) => f.active).reduce((s, f) => s + f.monthly_contribution, 0)
  const down = funds.filter(isDown).length
  const off = funds.filter(sipOff).length
  // Value-weighted, and only across the funds that actually report an XIRR — a fund priced off an
  // assumed rate has no realised return to average in, and quietly including it is how a portfolio
  // gets reported above reality.
  const withXirr = funds.filter((f) => typeof f.xirr_pct === 'number')
  const xirrWeight = withXirr.reduce((s, f) => s + f.current_value, 0)
  const xirr =
    withXirr.length > 0 && xirrWeight > 0
      ? withXirr.reduce((s, f) => s + (f.xirr_pct as number) * f.current_value, 0) / xirrWeight
      : null
  return {
    funds,
    value,
    contributed,
    gain: value - contributed,
    sip,
    down,
    off,
    xirr,
    xirrCount: withXirr.length,
  }
}
