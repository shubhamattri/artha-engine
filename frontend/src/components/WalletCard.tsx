import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchWallet, formatINR } from '../api/client'
import { spendFromWallet } from '../api/wallet'
import { useCan } from '../auth/Can'
import { AddPanel, Card, ErrorState, FormRow, Loading, SubmitButton, TextInput } from './ui'

/**
 * Taking money out for something that is not a loan payment or an SIP.
 *
 * Those two already debit the wallet as part of recording the payment, which is right — one act,
 * one entry. Without this, everything else the saved money went on could only leave the wallet by
 * not being recorded at all, and the balance drifted away from the bank.
 */
function TakeOut({ balance }: { balance: number }) {
  const qc = useQueryClient()
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [on, setOn] = useState(new Date().toISOString().slice(0, 10))
  const [err, setErr] = useState<string | null>(null)

  const spend = useMutation({
    mutationFn: () => spendFromWallet({ amount: Number(amount), note: note || null, on }),
    onSuccess: () => {
      setErr(null); setAmount(''); setNote('')
      // The wallet is money already counted in net worth, so nothing else moves — but the plan
      // reads the balance when it proposes paying a loan from it.
      ;['wallet', 'debt', 'cockpit'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
    },
    // "the wallet holds X, cannot spend Y" — the server names both figures, which is the answer.
    onError: (x: unknown) => setErr(
      (x as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      ?? 'Could not record that.'),
  })

  const n = Number(amount)
  const tooMuch = Number.isFinite(n) && n > balance

  return (
    <AddPanel label="Take money out">
      <FormRow cols={3} onSubmit={(e) => { e.preventDefault(); spend.mutate() }}
        submit={<SubmitButton disabled={!n || n <= 0 || tooMuch || spend.isPending}>
          {spend.isPending ? 'Recording…' : 'Record it'}
        </SubmitButton>}>
        <TextInput label="Amount" inputMode="decimal" value={amount}
          onChange={(e) => setAmount(e.target.value)}
          hint={tooMuch ? `The wallet holds ${formatINR(balance)}` : undefined} />
        <TextInput label="What for" placeholder="what the money went on" value={note}
          onChange={(e) => setNote(e.target.value)} />
        <TextInput label="Date" type="date" value={on} onChange={(e) => setOn(e.target.value)} />
      </FormRow>
      {err && <p className="mt-2 text-[11.5px] text-negative">{err}</p>}
    </AddPanel>
  )
}

/**
 * What past cycles underspent, and where it has gone.
 *
 * The balance was computed each cycle, shown once, then vanished when the cycle rolled — so
 * "we saved money last month, what happened to it?" had no answer anywhere in the app.
 *
 * It is NOT a new pot of money and the card says so plainly. Those rupees are already in a bank
 * account whose balance net worth already counts; presenting it as an extra asset would tell you
 * you are richer than you are.
 */
export default function WalletCard() {
  const mayEdit = useCan('plan.lines', 'edit')
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['wallet'], queryFn: fetchWallet })

  if (isLoading) return <Loading rows={2} />
  // Returning null made a broken fetch look exactly like an empty wallet, so the carried-forward
  // balance silently ceased to exist on the page instead of saying it could not be read. That is the
  // same question this card was built to answer.
  if (isError || !data) return <ErrorState message="Couldn’t load your wallet." onRetry={() => refetch()} />

  if (!data.starts_from) {
    return (
      <Card className="p-4 text-[13px] text-ink-2">
        The wallet has not started yet. It begins with the first cycle that closes after you turn it
        on — earlier cycles are left out because there is not enough recorded to say what they saved.
      </Card>
    )
  }

  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-ink-2">Wallet</span>
        <span className="money text-xl font-medium text-ink">{formatINR(data.balance)}</span>
      </div>
      <p className="mt-1 text-[11px] text-ink-2">{data.note}</p>

      {data.inflows.length > 0 && (
        <div className="mt-3 border-t border-line pt-2">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-2">Put in</div>
          {data.inflows.map((i) => (
            <div key={i.cycle_end} className="flex items-center justify-between py-0.5 text-[13px]">
              <span className="text-ink-2">cycle ending {i.cycle_end}</span>
              <span className="money text-positive">+{formatINR(i.saved)}</span>
            </div>
          ))}
        </div>
      )}

      {data.outflows.length > 0 && (
        <div className="mt-2 border-t border-line pt-2">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-2">Taken out</div>
          {data.outflows.map((o) => (
            <div key={o.id} className="flex items-center justify-between py-0.5 text-[13px]">
              {/* what it paid for, not just that it left — the whole point of the ledger */}
              <span className="text-ink-2">{o.note || (o.loan_id ? `loan #${o.loan_id}` : 'withdrawal')} · {o.on}</span>
              <span className="money text-ink">−{formatINR(o.amount)}</span>
            </div>
          ))}
        </div>
      )}

      {data.outflows.length === 0 && (
        <p className="mt-2 text-[11px] text-ink-2">
          Nothing spent from it yet. On any loan payment, choose <span className="text-ink">Pay from
          → Wallet</span>.
        </p>
      )}

      {mayEdit && data.balance > 0 && (
        <div className="mt-3 border-t border-line pt-3"><TakeOut balance={data.balance} /></div>
      )}
    </Card>
  )
}
