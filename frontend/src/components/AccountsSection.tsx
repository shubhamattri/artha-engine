import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchAccounts, fetchUnknownSenders } from '../api/client'
import AccountsManager from './AccountsManager'
import { Card, Icon } from './ui'

/**
 * Collapsed entry point for accounts, sitting next to statement upload on Spending.
 *
 * It lives here rather than only under the profile menu because this is the page you are already on
 * when you wonder where a transaction came from — a settings screen nobody can find is the same as
 * no screen. Collapsed by default so the page stays about spending, but the header carries the count
 * and, when something is unrecognised, an amber badge: the one case where you need to act is visible
 * without opening anything.
 */
export default function AccountsSection() {
  const [open, setOpen] = useState(false)
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: fetchAccounts })
  const senders = useQuery({ queryKey: ['unknown-senders'], queryFn: fetchUnknownSenders })

  const n = accounts.data?.accounts.length ?? 0
  const unknown = senders.data?.unknown.length ?? 0

  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-11 w-full items-center justify-between gap-3 text-left"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink">Accounts &amp; cards</span>
            {unknown > 0 && (
              <span className="rounded-chip bg-warn/15 px-2 py-0.5 text-[10px] font-bold text-warn">
{unknown} not tracked
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-ink-3">
            {n === 0
              ? 'Add a card or account so its alerts are captured'
              : `${n} tracked${unknown > 0 ? ` · ${unknown} bank sender${unknown > 1 ? 's' : ''} not matched to any card` : ' · alerts captured automatically'}`}
          </div>
        </div>
        <Icon name="arrow" size={16} className={open ? 'shrink-0 rotate-90' : 'shrink-0'} />
      </button>

      {open && (
        <div className="mt-4">
          <AccountsManager />
        </div>
      )}
    </Card>
  )
}
