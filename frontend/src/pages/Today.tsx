/**
 * TAB 1 · Today — the daily open. Safe to spend, what's due, the one move, what needs you.
 *
 * Commitments is NOT here. It was, and that was the mistake: the whole page got pasted below Cockpit,
 * which put "Paid · 11 / Missing · 12 / Not tracked · 7" on the screen opened ten times a day to ask
 * one question. The design is explicit — "Coming up ... taps through to Commitments" — so Coming up is
 * the summary and Commitments is a destination behind it.
 *
 * A tab is one screen with one hero and named sections, not a stack of the pages it replaced.
 */
import Cockpit from './Cockpit'

export default function Today() {
  return <Cockpit />
}
