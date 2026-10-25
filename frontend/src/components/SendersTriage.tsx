import { useState } from 'react'

import { type UnknownSender } from '../api/client'
import { useCan } from '../auth/Can'
import { Card, Icon, SectionHeader } from './ui'

/**
 * The senders inbox, triaged.
 *
 * Every sender the phone sees that matches no account is a stream of transactions being skipped,
 * and every figure in this app stands on that data. The list had 27 of them, each rendered
 * identically with an identical "This is my account" button, so it read as 27 equally-weighted
 * decisions and nobody finishes 27 of those. It also silently showed only the first 8.
 *
 * They are not equal. Most are one-time codes and delivery notices; only a few carry money. Sorting
 * them into three groups turns the list into a small number of real decisions plus two bulk
 * dismissals.
 *
 * Classification is by the SHAPE of the message, never by a list of brand names. A hardcoded
 * merchant list is right for one household and wrong for the next one, and it silently
 * misclassifies every merchant nobody thought of — which is the same failure as the name-keyed
 * colour map. Order matters: a one-time code is unambiguous, an order notice outranks an amount
 * (a delivery message quoting a total is still not a bank), and money is what is left.
 */
const RE_CODE = /\b(otp|one[- ]time (code|password)|verification code|do not share|maintenance|scheduled|offer|% off|sale ends|expires|reminder to)\b/i
const RE_ORDER = /\b(order|delivery|arriving|out for delivery|shipped|dispatch|tracking|invoice|receipt|purchase|your (parcel|package)|share code)\b/i
// The bullet is deliberate: this app masks digits in the sample it returns, so a real premium debit
// arrives as "Rs.•••.35". Matching only \d meant two Axis Max Life payments were read as having no
// amount at all and fell through to the codes pile.
const RE_AMOUNT = /(?:₹|rs\.?|inr)\s?[\d,•]+(?:\.[\d•]{1,2})?/i
const RE_MONEY = /\b(debit|debited|credit|credited|withdrawn|deposited|transferred|settlement|available balance|avl bal|a\/c|acct|account ending|txn|transaction|spent|charged|payment|premium|policy)\b/i

export type SenderGroup = 'money' | 'order' | 'code' | 'unclear'

/**
 * Note what the FALLBACK is. It was `code`, which put anything the rules did not recognise into the
 * one group that is collapsed by default and offered a bulk dismiss — so an unrecognised money
 * sender would be hidden and then dismissed in one click, which is worse than the undifferentiated
 * list this screen replaced. Measured against the real 27: two Axis Max Life premium debits landed
 * there. Unrecognised now means unrecognised, in its own visible group, with no bulk action.
 */
export function classifySender(u: UnknownSender): SenderGroup {
  const s = u.sample || ''
  if (RE_CODE.test(s)) return 'code'
  if (RE_ORDER.test(s)) return 'order'
  if (RE_AMOUNT.test(s) && RE_MONEY.test(s)) return 'money'
  return 'unclear'
}

function Row({ u, action }: { u: UnknownSender; action?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-t border-line px-1 py-2.5 first:border-0">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-ink">{u.sender}</div>
        <div className="money mt-0.5 text-[11px] tabular-nums text-ink-3">
          {u.count} message{u.count > 1 ? 's' : ''} · last {u.last_seen.slice(0, 10)}
        </div>
        {u.sample && <div className="mt-1 truncate text-[11px] text-ink-3">{u.sample}</div>}
      </div>
      {action}
    </div>
  )
}

export default function SendersTriage({ unknown, onClaim }: {
  unknown: UnknownSender[]
  onClaim: (u: UnknownSender) => void
}) {
  const [showCodes, setShowCodes] = useState(false)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const mayEdit = useCan('ledger.import', 'edit')

  const live = unknown.filter((u) => !dismissed.has(u.sender))
  const money = live.filter((u) => classifySender(u) === 'money')
  const orders = live.filter((u) => classifySender(u) === 'order')
  const codes = live.filter((u) => classifySender(u) === 'code')
  const unclear = live.filter((u) => classifySender(u) === 'unclear')
  const msgs = (list: UnknownSender[]) => list.reduce((a, u) => a + u.count, 0)
  const dismiss = (list: UnknownSender[]) =>
    setDismissed((d) => new Set([...d, ...list.map((u) => u.sender)]))

  if (!live.length) return null

  // Claiming a sender does one thing: prefill the add-account form. Someone who cannot add an
  // account would be walked to a form whose Save the server refuses. The dismissals stay — local.
  const claimBtn = (u: UnknownSender) => !mayEdit ? null : (
    <button onClick={() => onClaim(u)}
      className="inline-flex min-h-11 shrink-0 items-center rounded-input border border-accent px-3 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft">
      This is my account
    </button>
  )

  return (
    <Card>
      <SectionHeader
        level={2}
        title="Messages we can't match to a card"
        sub={`${live.length} senders, ${msgs(live)} messages. Anything they record is being skipped, and every figure in the app is built on what does get recorded. They are sorted by what they look like, so there are only a few real decisions here.`}
      />

      {money.length > 0 && (
        <div className="mt-4">
          <div className="tracked text-[10px] font-semibold text-ink-3">
            Looks like money · {money.length}
          </div>
          <p className="mt-1 text-[11.5px] leading-relaxed text-ink-2">
            The only real decisions on this page. Each one&rsquo;s most recent message carries an
            amount and reads like a bank. If it is yours, naming it starts capturing{' '}
            <span className="money font-semibold text-ink">{msgs(money)}</span> messages.
          </p>
          <div className="mt-2">
            {money.map((u) => <Row key={u.sender} u={u} action={claimBtn(u)} />)}
          </div>
        </div>
      )}

      {orders.length > 0 && (
        <div className="mt-5">
          <div className="tracked text-[10px] font-semibold text-ink-3">
            Order &amp; delivery notices · {orders.length}
          </div>
          {/* The warning is the point of this group, not the rows. Someone working down an
              undifferentiated list will happily claim a food-delivery sender as an account, and
              then that spend is counted twice: once from the card that actually paid, once from
              the receipt. */}
          <div className="mt-1.5 flex items-start gap-2 rounded-xl border border-line px-3 py-2">
            <span className="mt-0.5 shrink-0 text-warn"><Icon name="alert" size={14} /></span>
            <p className="text-[11.5px] leading-relaxed text-ink-2">
              These are receipts, not accounts. Adding one double-counts spend the card that paid
              for it has already recorded.
            </p>
          </div>
          <div className="mt-2">
            {orders.map((u) => <Row key={u.sender} u={u} />)}
          </div>
          <button onClick={() => dismiss(orders)}
            className="mt-2 inline-flex min-h-11 items-center text-xs font-semibold text-ink-3 hover:text-ink">
            Dismiss all {orders.length}
          </button>
        </div>
      )}

      {unclear.length > 0 && (
        <div className="mt-5">
          <div className="tracked text-[10px] font-semibold text-ink-3">
            Can&rsquo;t tell &middot; {unclear.length}
          </div>
          <p className="mt-1 text-[11.5px] leading-relaxed text-ink-2">
            The most recent message from these does not read clearly as money, an order or a code.
            Shown rather than filed, and deliberately with no bulk dismiss &mdash; guessing wrong here
            hides a real account.
          </p>
          <div className="mt-2">
            {unclear.map((u) => <Row key={u.sender} u={u} action={claimBtn(u)} />)}
          </div>
        </div>
      )}

      {codes.length > 0 && (
        <div className="mt-5 border-t border-line pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <button onClick={() => setShowCodes((v) => !v)}
              className="inline-flex min-h-11 items-center gap-1.5 text-xs font-semibold text-ink-2 hover:text-ink"
              aria-expanded={showCodes}>
              <Icon name="chevron" size={14} className={showCodes ? 'rotate-90' : ''} />
              One-time codes &amp; notices · {codes.length}
            </button>
            <button onClick={() => dismiss(codes)}
              className="inline-flex min-h-11 items-center text-xs font-semibold text-ink-3 hover:text-ink">
              Dismiss all {codes.length}
            </button>
          </div>
          {showCodes && (
            <div className="mt-1">
              {codes.map((u) => <Row key={u.sender} u={u} action={claimBtn(u)} />)}
            </div>
          )}
        </div>
      )}

      {/* Collapsed, never truncated. The list used to cut off at 8 with nothing saying so, which
          reads as "that is all of them" while 19 senders went unlooked-at. Each group is expandable
          and each dismissal is per-session — nothing here deletes anything on the server. */}
      <p className="mt-4 text-[11px] leading-relaxed text-ink-3">
        Sorted by the most recent message from each sender, which is all this screen can see.
        Dismissing hides a group for now; it does not change anything on your phone or in your data.
      </p>
    </Card>
  )
}
