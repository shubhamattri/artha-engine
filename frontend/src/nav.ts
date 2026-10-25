import { type IconName } from './components/ui/Icon'

/**
 * The app's navigation, in ONE place.
 *
 * It used to live twice — a TABS array in ui/BottomNav for phones and a NAV array in Layout for
 * desktop — with a comment on one of them asking whoever edits it to keep the other in lockstep by
 * hand. Two hand-synced copies of the same list is a drift waiting to happen: add a tab and the
 * phone and the desktop disagree about what this app contains, and nothing fails to tell you.
 *
 * FOUR tabs, down from seven. Seven slots at 375pt is 53px each, which forced labels to 8px and
 * still left "This month" barely clearing its slot; four gives 93px and puts labels back to 11px.
 * The deeper problem was that seven peers means ranking them yourself on every open — every tab was
 * built as a home for a DOMAIN rather than an answer to a question, so they all had the same shape
 * and none of them was the obvious place to look.
 *
 * Ordered by how often the question is asked, not by how much data sits behind it: Today many times
 * a day, Spending daily, Plan monthly, Balance standing.
 *
 * Windfall and Ask Artha are deliberately NOT here. They are tools you invoke — a lump sum landing
 * is a twice-a-year event, and Ask Artha cuts across every tab, so it holds a persistent affordance
 * in the header instead of a slot competing with places.
 *
 * `icon` is only used by the phone nav; desktop renders labels alone.
 */
export const NAV: { to: string; label: string; icon: IconName }[] = [
  { to: '/today', label: 'Today', icon: 'home' },
  { to: '/spending', label: 'Spending', icon: 'chart' },
  { to: '/plan', label: 'Plan', icon: 'list' },
  { to: '/balance', label: 'Balance', icon: 'wallet' },
]
