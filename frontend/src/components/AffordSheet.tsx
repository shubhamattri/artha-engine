import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sheet, TextInput, SelectInput, Icon } from './ui'
import { fetchTransactionFacets, fetchCategories, shortINR as inr, currencySymbol } from '../api/client'
import { quickAdd } from '../api/afford'
import { ViewOnly, useCan } from '../auth/Can'

/** Quick log of a spend — the daily driver. "Can I afford it?" lives in Ask Artha now (chat). */
export default function AffordSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const mayEdit = useCan('spending', 'edit')
  const facets = useQuery({ queryKey: ['txn-facets'], queryFn: fetchTransactionFacets, enabled: open })
  const cats = useQuery({ queryKey: ['categories'], queryFn: fetchCategories, enabled: open })
  // "Paid from" is 17 accounts on an iOS wheel picker, two of them sharing a display name, with no
  // default — on the app's most frequent action. Remember the last one used and the last category,
  // so the common case is confirm-and-go instead of scroll-and-hunt.
  const [amount, setAmount] = useState('')
  const [category, setCategory] = useState(() => localStorage.getItem('artha.lastCategory') ?? '')
  const [account, setAccount] = useState(() => localStorage.getItem('artha.lastAccount') ?? '')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [logged, setLogged] = useState(false)

  const CATS = (cats.data ?? []).filter((c) => c.is_spend).map((c) => c.value)
  const accountOptions = [...(facets.data?.cards ?? []).map((c) => c.label), 'Cash']
  const amt = parseFloat(amount)
  // Amount and note are per-spend; category and account are sticky on purpose (see above).
  const reset = () => { setAmount(''); setNote(''); setLogged(false) }
  const close = () => { reset(); onClose() }

  async function log() {
    if (!amt || amt <= 0) return
    setBusy(true)
    try {
      await quickAdd(amt, category || undefined, note || undefined, account || undefined)
      if (account) localStorage.setItem('artha.lastAccount', account)
      if (category) localStorage.setItem('artha.lastCategory', category)
      setLogged(true)
      ;['cockpit', 'txns', 'txn-facets', 'cashflow', 'budget', 'insights', 'health'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
      setTimeout(close, 900)
    } finally { setBusy(false) }
  }

  return (
    <Sheet open={open} onClose={close} title="Log a spend">
      {!mayEdit ? <ViewOnly what="this household's spending" /> : logged ? (
        <div className="flex flex-col items-center py-8 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent"><Icon name="check" size={26} /></span>
          <div className="mt-3 text-sm font-semibold text-ink">Logged <span className="money">{inr(amt)}</span></div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <TextInput label="Amount" placeholder={`${currencySymbol()}0`} inputMode="decimal" autoFocus
                value={amount} onChange={(e) => setAmount(e.target.value)} />
            </div>
            <SelectInput label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Choose…</option>
              {CATS.map((c) => <option key={c} value={c}>{c}</option>)}
            </SelectInput>
            <SelectInput label="Paid from" value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">Choose…</option>
              {accountOptions.map((a) => <option key={a} value={a}>{a}</option>)}
            </SelectInput>
            <div className="sm:col-span-2">
              <TextInput label="Note (optional)" placeholder="e.g. lunch at Swiggy"
                value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>
          <button onClick={log} disabled={!amt || busy}
            className="min-h-11 w-full rounded-input bg-accent text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50">
            {busy ? 'Logging…' : 'Log spend'}
          </button>
          <p className="text-center text-[11px] text-ink-3">Want to know if you can afford something? Ask Artha ✦ (top right).</p>
        </div>
      )}
    </Sheet>
  )
}
