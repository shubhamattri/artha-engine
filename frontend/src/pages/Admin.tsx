import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { deactivateUser, fetchAdminUsers, setUserPermissions, setUserRole, type AdminUser }
  from '../api/admin'
import { useAuth } from '../auth/AuthContext'
import { Card, ErrorState, Loading, PageContainer, SectionHeader, useConfirm } from '../components/ui'

/**
 * Who is in this household, what they may do, and the one place to change it.
 *
 * Super admin only, and the SERVER enforces that — every route here carries `require_super_admin`.
 * Hiding this screen from everyone else is tidiness; it is not what keeps them out.
 */
const LEVELS = [
  { v: 0, label: 'None' },
  { v: 1, label: 'View' },
  { v: 2, label: 'Edit' },
] as const

/** Plain-language names. The keys are for the code; nobody configuring their father's access should
 *  have to read `ledger.housekeeping` and work out what it covers. */
const KEY_LABELS: Record<string, string> = {
  'spending': 'Spending and transactions',
  'ledger.import': 'Statements, sync and accounts',
  'ledger.housekeeping': 'Duplicates and bulk tidying',
  'budget': 'Budget targets',
  'categories': 'Categories',
  'plan.lines': 'Income, fixed costs and sinking funds',
  'debt': 'Loans and the payoff plan',
  'wealth': 'Assets, investments and insurance',
  'commitments': 'Commitments',
  'windfall': 'Windfall planning',
  'household.admin': 'Household settings',
  'advisor': 'Ask Artha',
}

const SENSITIVE = new Set(['ledger.import'])

export default function Admin() {
  const { managesUsers, user: me } = useAuth()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [busy, setBusy] = useState<number | null>(null)

  const q = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: fetchAdminUsers,
    enabled: managesUsers,
    retry: false,
  })

  const setRole = useMutation({
    mutationFn: ({ id, role }: { id: number; role: string }) => setUserRole(id, role),
    onSettled: () => { setBusy(null); qc.invalidateQueries({ queryKey: ['admin', 'users'] }) },
  })

  const setGrants = useMutation({
    mutationFn: ({ id, grants }: { id: number; grants: { key: string; level: number }[] }) =>
      setUserPermissions(id, grants),
    onSettled: () => { setBusy(null); qc.invalidateQueries({ queryKey: ['admin', 'users'] }) },
  })

  const removeAccess = useMutation({
    mutationFn: (id: number) => deactivateUser(id),
    onSettled: () => { setBusy(null); qc.invalidateQueries({ queryKey: ['admin', 'users'] }) },
  })

  if (!managesUsers) {
    return (
      <PageContainer>
        <SectionHeader title="People" sub="Only the household's super admin can manage people." />
      </PageContainer>
    )
  }

  return (
    <PageContainer>
      <SectionHeader
        title="People"
        sub="Who can sign in, and what each of them can change. Everyone sees the whole household picture; this decides who can edit it."
      />

      {q.isLoading && <Loading rows={3} />}
      {q.isError && <ErrorState message="Couldn’t load the people in this household."
                                onRetry={() => q.refetch()} />}

      {q.data?.users.map((u) => {
        const elevated = u.role === 'admin' || u.role === 'super_admin'
        const isSelf = u.id === me?.id
        return (
          <Card key={u.id} className={u.is_active ? '' : 'opacity-60'}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <div className="min-w-0">
                <div className="truncate font-semibold text-ink">
                  {u.name}{isSelf ? ' (you)' : ''}
                </div>
                <div className="truncate text-xs text-ink-3">{u.email}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <select
                  value={u.role}
                  disabled={busy === u.id || u.role === 'super_admin'}
                  onChange={(e) => { setBusy(u.id); setRole.mutate({ id: u.id, role: e.target.value }) }}
                  className="min-h-11 rounded-input border border-line bg-surface px-3 text-sm text-ink"
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                  {u.role === 'super_admin' && <option value="super_admin">Super admin</option>}
                </select>
                {/* Not offered on your own row: the server refuses it, and locking the only person who
                    can manage people out of the household is not an accident worth enabling. */}
                {u.is_active && !isSelf && (
                  <button
                    disabled={busy === u.id}
                    onClick={async () => {
                      if (await confirm({
                        title: `Remove ${u.name}’s access?`,
                        message: 'They can no longer sign in. Nothing they recorded is removed — their transactions stay as household history, and their name stays on the household. This cannot be undone from here.',
                        confirmLabel: 'Remove access',
                        danger: true,
                      })) { setBusy(u.id); removeAccess.mutate(u.id) }
                    }}
                    className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-negative disabled:opacity-50"
                  >
                    Remove access
                  </button>
                )}
              </div>
            </div>

            {removeAccess.isError && removeAccess.variables === u.id && (
              <p className="mt-2 text-xs font-medium text-negative">
                {(removeAccess.error as { response?: { data?: { detail?: string } } })
                  ?.response?.data?.detail ?? 'Could not remove that login.'}
              </p>
            )}

            {!u.is_active && (
              <p className="mt-2 text-[11.5px] leading-relaxed text-ink-2">
                Can’t sign in. Everything they recorded is still here. Giving the login back isn’t
                something this screen can do yet.
              </p>
            )}

            {u.role === 'super_admin' && (
              <p className="mt-2 text-[11.5px] leading-relaxed text-ink-2">
                Holds every permission and is the only person who can change these settings. One per
                household, so this cannot be handed over from here.
              </p>
            )}

            {u.role === 'admin' && (
              <p className="mt-2 text-[11.5px] leading-relaxed text-ink-2">
                Can edit everything in the household. Cannot manage people or permissions.
              </p>
            )}

            {u.role === 'member' && (
              <div className="mt-3 border-t border-line pt-3">
                <div className="tracked text-[10px] font-semibold text-ink-3">What they can change</div>
                <div className="mt-2 space-y-1">
                  {(q.data?.keys ?? []).map((key) => (
                    <label key={key} className="flex min-h-11 items-center justify-between gap-3">
                      <span className="min-w-0 flex-1 truncate text-sm text-ink">
                        {KEY_LABELS[key] ?? key}
                        {SENSITIVE.has(key) && (
                          <span className="ml-1.5 text-[11px] text-warn">
                            reads your Messages and mailbox
                          </span>
                        )}
                      </span>
                      <select
                        value={u.permissions[key] ?? 0}
                        disabled={busy === u.id || elevated}
                        onChange={(e) => {
                          setBusy(u.id)
                          const grants = (q.data?.keys ?? []).map((k) => ({
                            key: k,
                            level: k === key ? Number(e.target.value) : (u.permissions[k] ?? 0),
                          }))
                          setGrants.mutate({ id: u.id, grants })
                        }}
                        className="min-h-11 shrink-0 rounded-input border border-line bg-surface px-2 text-xs text-ink"
                      >
                        {LEVELS.map((l) => <option key={l.v} value={l.v}>{l.label}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </Card>
        )
      })}

      <p className="text-[11px] leading-relaxed text-ink-3">
        Changing something here takes effect on their next request. Hiding a control in the app is a
        courtesy; the server refuses the action either way.
      </p>
    </PageContainer>
  )
}
