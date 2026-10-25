import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useConfig } from '../api/meta'
import { askAdvisor, fetchDebt, type ChatMsg } from '../api/client'
import { useCockpit } from '../api/cockpit'
import { usePeriod } from '../hooks/usePeriod'
import PeriodToggle from '../components/ui/PeriodToggle'
import AffordCheck from '../components/AffordCheck'
import { ViewOnly, useCan } from '../auth/Can'

const STORE = 'artha-chat'
function loadChat(): ChatMsg[] {
  try { return JSON.parse(localStorage.getItem(STORE) || '[]') } catch { return [] }
}

/**
 * Suggested questions built from what is actually true right now, not a fixed list.
 *
 * A pace warning offered on a cycle that is comfortably inside its target teaches the reader that
 * these prompts are decoration; the one time it matters they will not read it either. So each
 * prompt has a condition, and the list falls back to the evergreen strategic questions when nothing
 * is wrong — which is itself information.
 */
function suggestions(pace: CockpitPace | null, topLoan: string | null): string[] {
  const out: string[] = []
  if (pace && pace.state === 'over') {
    out.push('I am over my target this cycle — what should I cut first?')
  } else if (pace && pace.days_early != null && pace.days_early > 0) {
    out.push(`At this pace I run out ${pace.days_early} days early. What is driving it?`)
  }
  if (topLoan) out.push(`Should I clear ${topLoan} early, or invest the money instead?`)
  out.push('How do I free up more income each month?')
  out.push('What should I liquidate, and what should I keep?')
  return out.slice(0, 4)
}

type CockpitPace = { state?: string; days_early?: number | null }

/** Ask Artha — chat with a CA that has your full financial context. Strategic, not yes/no. */
export default function AskArtha() {
  const brand = useConfig().data?.brand_name ?? 'Artha'
  // Asking is the only thing this screen does, so without it there is no composer and no chips —
  // one sentence at the top beats a text box whose Send would come back refused.
  const mayAsk = useCan('advisor', 'edit')
  const [msgs, setMsgs] = useState<ChatMsg[]>(loadChat)   // persisted — survives navigation/reload
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [period] = usePeriod()                            // shared global lens — same as Plan/Spending/Analytics
  const endRef = useRef<HTMLDivElement>(null)
  // Silent on failure, both of them: a prompt list is not worth an error state on a chat screen.
  const cockpit = useCockpit(period)
  const debt = useQuery({ queryKey: ['debt'], queryFn: fetchDebt, retry: false })
  const topLoan = debt.data?.strategies?.options?.find((o) => o.key === debt.data?.strategies?.recommended)
    ?.order?.[0]?.name ?? null
  const prompts = suggestions((cockpit.data?.pace ?? null) as CockpitPace | null, topLoan)

  useEffect(() => { localStorage.setItem(STORE, JSON.stringify(msgs)) }, [msgs])

  async function send(text: string) {
    const q = text.trim()
    if (!q || busy) return
    const next: ChatMsg[] = [...msgs, { role: 'user', content: q }]
    setMsgs(next); setInput(''); setBusy(true)
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    try {
      const { reply } = await askAdvisor(next, period)    // answer in the lens the user is viewing
      setMsgs([...next, { role: 'assistant', content: reply }])
    } catch {
      setMsgs([...next, { role: 'assistant', content: "Sorry — I couldn't reach the advisor just now." }])
    } finally {
      setBusy(false)
      setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    }
  }

  return (
    // Messages flow + scroll inside the main scroll area; the composer is PINNED to the bottom (just
    // above the mobile nav) with `fixed`, so it never drifts up/down as the conversation grows. The
    // bottom padding keeps the last message clear of the pinned bar.
    <div className={`mx-auto w-full max-w-2xl px-4 ${mayAsk ? 'pb-[132px] md:pb-24' : 'pb-10'}`}>
      <div className="flex items-center justify-between pb-1 pt-4">
        <div>
          <h1 className="text-xl font-extrabold tracking-tight text-ink">Ask {brand}</h1>
          <p className="text-xs text-ink-2">Your CA — full picture of income, loans, net worth, spend.</p>
        </div>
        {msgs.length > 0 && (
          <button onClick={() => { setMsgs([]); localStorage.removeItem(STORE) }}
            className="shrink-0 text-xs font-semibold text-ink-3 hover:text-ink">New chat</button>
        )}
      </div>

      {!mayAsk && <div className="pt-2"><ViewOnly what="this chat" /></div>}

      {/* lens toggle — same global "This cycle | By month" switch as Plan/Spending/Analytics; Artha
          frames 'this period' answers in whichever is selected */}
      <div className="flex items-center justify-between pb-2 pt-1">
        <PeriodToggle />
        <span className="text-[10.5px] text-ink-3">
          {period === 'cycle' ? 'Answers use your billing cycle' : 'Answers use the calendar month'}
        </span>
      </div>

      {/* The one question with a computed answer, above the chat that can only offer a reading. The
          Log-a-spend sheet already sends people here for it; until now nothing on this screen ran it. */}
      <AffordCheck />

      {/* make the context visible — so it's clear Artha isn't answering blind */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        {['Income', 'Loans', 'Net worth', period === 'cycle' ? 'This cycle’s spend' : "This month’s spend"].map((c) => (
          <span key={c} className="rounded-chip bg-surface-2 px-2 py-0.5 text-[10.5px] font-medium text-ink-3">✦ {c}</span>
        ))}
      </div>

      <div className="space-y-3">
        {mayAsk && msgs.length === 0 && (
          <div className="space-y-2">
            <div className="text-xs font-semibold text-ink-3">Try asking</div>
            {prompts.map((s) => (
              <button key={s} onClick={() => send(s)}
                className="block w-full rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm text-ink hover:border-accent">
                {s}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          m.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-accent px-4 py-2.5 text-sm leading-relaxed text-white">
                {m.content}
              </div>
            </div>
          ) : (
            /* An answer here is a READING, and it has to look unlike every other surface in the app
               or it spends the credibility the computed screens earned. Dashed border and a
               surface-2 fill, never the solid card a figure gets. The handoff splits an answer into
               "Computed from your data" and "Artha's reading"; the endpoint returns one block of
               prose with no facts separated out, so the honest treatment is to mark ALL of it as
               the reading rather than dress narration as calculation. See BLOCKED.md. */
            <div key={i} className="rounded-2xl border border-dashed border-line-2 bg-surface-2 px-4 py-3">
              <span className="inline-flex items-center gap-1.5 rounded-chip border border-line bg-surface px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[.1em] text-ink-3">
                {brand}&rsquo;s reading
              </span>
              <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">{m.content}</div>
              <p className="mt-3 border-t border-dashed border-line-2 pt-2.5 text-[11px] leading-relaxed text-ink-3">
                This is a reading, not a calculation. The figures on your other screens are firm;
                this is an attempt at what they mean.
              </p>
            </div>
          )
        ))}

        {/* Provenance: what was read, and what was NOT. The second half is the part that matters —
            an answer that never saw your transactions cannot be wrong about them, and cannot be
            right about them either. Taken from what services/advisor.py actually puts in the
            snapshot, not from what the screen would like to claim. */}
        {msgs.some((m) => m.role === 'assistant') && (
          <p className="px-1 text-[11px] leading-relaxed text-ink-3">
            Read: your income and how it is allocated, every loan with its rate, net worth by type,
            and this cycle&rsquo;s and this month&rsquo;s spend. Not read: individual transactions,
            your commitments list, the year view, or anything you have not recorded.
          </p>
        )}
        {busy && <div className="flex justify-start"><div className="rounded-2xl border border-line bg-surface px-4 py-2.5 text-sm text-ink-3">Thinking…</div></div>}
        <div ref={endRef} />
      </div>

      {/* pinned composer — sits above the mobile nav (~58px); flush to the bottom on desktop */}
      {mayAsk && (
        <div className="fixed inset-x-0 z-30 border-t border-line backdrop-blur bottom-[calc(env(safe-area-inset-bottom)_+_58px)] md:bottom-0"
          style={{ background: 'color-mix(in srgb, var(--bg) 95%, transparent)' }}>
          <div className="mx-auto flex max-w-2xl gap-2 px-4 py-3">
            <input value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && send(input)} placeholder="Ask anything about your money…"
              className="flex-1 rounded-xl border border-line bg-surface px-4 py-3 text-ink outline-none focus:border-accent" />
            <button onClick={() => send(input)} disabled={!input.trim() || busy}
              className="shrink-0 rounded-xl bg-accent px-4 text-sm font-semibold text-white disabled:opacity-50">Send</button>
          </div>
        </div>
      )}
    </div>
  )
}
