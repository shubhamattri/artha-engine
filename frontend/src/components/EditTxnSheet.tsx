import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sheet, TextInput, SelectInput, Icon, useConfirm } from './ui'
import { ViewOnly, useCan } from '../auth/Can'
import History from './History'
import {
  fetchCategories,
  fetchTransactionFacets,
  updateTransaction,
  deleteTransaction,
  type Transaction,
} from '../api/client'

/** Correct or remove one transaction. The user is the final authority on their own data —
 *  changing the category re-derives whether it counts as spend (server-side), so a CC bill
 *  payment mis-filed as "Utilities" stops inflating the Plan the moment it's fixed. */
export default function EditTxnSheet({ txn, onClose }: { txn: Transaction | null; onClose: () => void }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const mayEdit = useCan('spending', 'edit')
  const cats = useQuery({ queryKey: ['categories'], queryFn: fetchCategories })
  const facets = useQuery({ queryKey: ['txn-facets'], queryFn: fetchTransactionFacets })

  const [amount, setAmount] = useState('')
  const [date, setDate] = useState('')
  const [merchant, setMerchant] = useState('')
  const [category, setCategory] = useState('')
  const [account, setAccount] = useState('')
  const [direction, setDirection] = useState<'debit' | 'credit'>('debit')
  const [busy, setBusy] = useState<'' | 'save' | 'archive' | 'delete'>('')
  const [err, setErr] = useState('')

  // hydrate fields whenever a new row is opened
  useEffect(() => {
    if (!txn) return
    setAmount(String(txn.amount))
    setDate(txn.date)
    setMerchant(txn.merchant ?? '')
    setCategory(txn.category ?? '')
    setAccount(txn.bank ?? '')
    setDirection(txn.direction)
    setBusy('')
    setErr('')
  }, [txn])

  // "Paid from" = the instrument the money left (card / bank account / cash). UPI is a rail, not an
  // account, so it isn't listed. Keep the row's current value so legacy entries still display.
  const accountOptions = Array.from(
    new Set([
      account,
      ...(facets.data?.cards ?? []).map((c) => c.label),
      'Cash',
    ].filter(Boolean)),
  )

  const amt = parseFloat(amount)

  async function save() {
    if (!txn || !amt || amt <= 0) return
    setBusy('save'); setErr('')
    try {
      await updateTransaction(txn.id, {
        amount: amt, date, merchant, category: category || undefined,
        account: account || undefined, direction,
      })
      invalidate()
      onClose()
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? 'Save failed')
    } finally { setBusy('') }
  }

  async function remove(hard: boolean) {
    if (!txn) return
    const ok = await confirm(hard ? {
      title: 'Delete permanently?',
      message: 'This removes the transaction completely and CANNOT be undone. Use this for a wrong entry (e.g. a mistyped manual log).',
      confirmLabel: 'Delete forever', danger: true,
    } : {
      title: 'Archive this transaction?',
      message: "It's hidden from all totals but kept in your history (recoverable). " +
        'Tip: to just keep it out of spend, set a non-spend category like "Settlement / Repayment" instead.',
      confirmLabel: 'Archive', danger: true,
    })
    if (!ok) return
    setBusy(hard ? 'delete' : 'archive'); setErr('')
    try {
      await deleteTransaction(txn.id, hard)
      invalidate()
      onClose()
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? (hard ? 'Delete failed' : 'Archive failed'))
    } finally { setBusy('') }
  }

  function invalidate() {
    ;['txns', 'txn-facets', 'budget', 'category-weeks', 'analytics-matrix', 'analytics-cycle-matrix', 'recommendations', 'cashflow', 'cockpit']
      .forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
  }

  return (
    <Sheet open={!!txn} onClose={onClose} title="Edit transaction">
      {txn && (
        <div className="space-y-4">
          <div className="rounded-input bg-surface-2 px-3 py-2 text-xs text-ink-3">
            {txn.description}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TextInput label="Amount" inputMode="decimal" value={amount}
              onChange={(e) => setAmount(e.target.value)} />
            <TextInput label="Date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <div className="sm:col-span-2">
              <TextInput label="Merchant" placeholder="clean name" value={merchant}
                onChange={(e) => setMerchant(e.target.value)} />
            </div>
            <SelectInput label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Choose…</option>
              {(cats.data ?? []).map((c) => (
                <option key={c.value} value={c.value}>{c.value}{c.is_spend ? '' : '  ·  not spend'}</option>
              ))}
            </SelectInput>
            <SelectInput label="Paid from" value={account} onChange={(e) => setAccount(e.target.value)}>
              {accountOptions.map((a) => <option key={a} value={a}>{a}</option>)}
            </SelectInput>
            <div className="sm:col-span-2">
              <SelectInput label="Direction" value={direction}
                onChange={(e) => setDirection(e.target.value as 'debit' | 'credit')}>
                <option value="debit">Money out (debit)</option>
                <option value="credit">Money in (credit)</option>
              </SelectInput>
            </div>
          </div>

          {err && <p className="rounded-input bg-negative/10 px-3 py-2 text-xs text-negative">{err}</p>}

          {/* The whole action block, or one sentence — not fifteen disabled controls. Someone who
              may only look still gets the full record; what changes is that nothing here offers
              them a button that the server would refuse. */}
          {!mayEdit ? <ViewOnly what="this transaction" /> : (
          <div className="pt-1">
            <button onClick={save} disabled={!amt || busy !== ''}
              className="min-h-11 w-full rounded-input bg-accent text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50">
              {busy === 'save' ? 'Saving…' : 'Save'}
            </button>
            <div className="mt-2 flex gap-3">
              <button onClick={() => remove(false)} disabled={busy !== ''}
                className="min-h-11 flex-1 rounded-input border border-line text-sm font-semibold text-ink-2 transition-colors hover:border-line-2 disabled:opacity-50">
                {busy === 'archive' ? 'Archiving…' : 'Archive'}
              </button>
              <button onClick={() => remove(true)} disabled={busy !== ''}
                className="flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-input border border-negative/40 text-sm font-semibold text-negative transition-colors hover:bg-negative/5 disabled:opacity-50">
                <Icon name="trash" size={16} /> {busy === 'delete' ? 'Deleting…' : 'Delete'}
              </button>
            </div>
            <p className="mt-1.5 text-center text-[11px] text-ink-3">Archive keeps it in history · Delete removes it for good</p>
          </div>
          )}

          {/* Collapsed by default. It is the answer to "why is this number what it is", which is a
              question asked occasionally and never while typing into the form above. */}
          <details className="border-t border-line pt-3">
            <summary className="min-h-11 cursor-pointer list-none text-[11.5px] font-semibold text-ink-2">
              History of this transaction ▸
            </summary>
            <div className="mt-2">
              <History txnId={txn.id} limit={50} />
            </div>
          </details>
        </div>
      )}
    </Sheet>
  )
}
