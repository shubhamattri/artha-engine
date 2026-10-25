import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchMembers, updateMemberIdentity, type MemberRow } from '../api/client'
import { Field, Icon, TextInput } from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { useCan } from '../auth/Can'

/**
 * Onboarding gate for the details banks require.
 *
 * Statement PDFs are locked with a password built from the holder's name, date of birth and
 * registered mobile. The app never asked, so statement import quietly failed for months and the only
 * symptom was reconciliation silently not happening. If a detail is load-bearing, onboarding has to
 * insist on it rather than discover it is missing later.
 *
 * Deliberately NOT a hard lockout. This is the household's own money; blocking the ledger because a
 * PAN is missing would be punishing them for our gap. It blocks on FIRST run only (nothing to see
 * yet anyway), and afterwards is dismissible for the session while staying visible in Settings.
 */
export default function OnboardingGate({ children }: { children: React.ReactNode }) {
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('household.admin', 'edit')
  const [dismissed, setDismissed] = useState(false)
  const [values, setValues] = useState<Record<number, { dob: string; mobile: string }>>({})

  const { data, isLoading, isError } = useQuery({ queryKey: ['members'], queryFn: fetchMembers })

  const save = useMutation({
    mutationFn: async (people: MemberRow[]) => {
      for (const m of people) {
        const v = values[m.id]
        if (!v) continue
        const body: Record<string, string> = {}
        if (v.dob?.trim()) body.dob = v.dob.trim()
        if (v.mobile?.trim()) body.mobile = v.mobile.trim()
        if (Object.keys(body).length) await updateMemberIdentity(m.id, body)
      }
    },
    onSuccess: refresh,
  })

  // Never hold the app hostage to a failed or in-flight lookup.
  if (isLoading || isError || !data) return <>{children}</>
  // Nor to a wall somebody cannot clear: filling these in is the household admin's job, and hiding
  // only the Save button would leave everyone else trapped on a full-screen form with no way out.
  if (!mayEdit) return <>{children}</>

  const people = data.members.filter((m) => !m.is_household)
  const blocked = people.filter((m) => (m.identity_missing_required?.length ?? 0) > 0)
  if (blocked.length === 0 || dismissed) return <>{children}</>

  const ready = blocked.every((m) => {
    const v = values[m.id]
    const need = m.identity_missing_required ?? []
    return need.every((k) => (v?.[k as 'dob' | 'mobile'] ?? '').trim().length > 0)
  })

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-surface px-4 py-8">
      <div className="mx-auto max-w-md">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-accent/12 text-accent">
            <Icon name="card" size={20} />
          </span>
          <h1 className="text-xl font-semibold text-ink">One thing before we start</h1>
        </div>

        <p className="mt-4 text-sm leading-relaxed text-ink-2">
          Your banks send monthly statements as password-protected PDFs. The password is built from
          each cardholder’s date of birth and registered mobile number, so without these we can’t open
          them, and your statements never get imported or reconciled.
        </p>
        <p className="mt-2 text-sm leading-relaxed text-ink-3">
          Stored on your own server and never sent anywhere. Once saved, they’re never shown again.
        </p>

        <div className="mt-8 space-y-6">
          {blocked.map((m) => {
            const need = m.identity_missing_required ?? []
            const v = values[m.id] ?? { dob: '', mobile: '' }
            const set = (k: 'dob' | 'mobile', val: string) =>
              setValues({ ...values, [m.id]: { ...v, [k]: val } })
            return (
              <div key={m.id} className="rounded-2xl border border-line bg-surface-2 p-4">
                <div className="text-sm font-semibold text-ink">{m.name}</div>
                <div className="mt-3 space-y-3">
                  {need.includes('dob') && (
                    <Field label="Date of birth" hint="As registered with the bank">
                      <TextInput value={v.dob} placeholder="1990-01-31" inputMode="numeric"
                        onChange={(e) => set('dob', e.target.value)} />
                    </Field>
                  )}
                  {need.includes('mobile') && (
                    <Field label="Mobile number" hint="The one registered with the bank">
                      <TextInput value={v.mobile} placeholder="9876543210" inputMode="numeric"
                        onChange={(e) => set('mobile', e.target.value)} />
                    </Field>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {save.isError && (
          <div className="mt-4 text-sm font-medium text-negative">
            {(save.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
              ?? 'Could not save. Check the values and try again.'}
          </div>
        )}

        <button
          onClick={() => save.mutate(blocked)}
          disabled={!ready || save.isPending}
          className="mt-8 min-h-11 w-full rounded-input bg-accent px-4 text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-40"
        >
          {save.isPending ? 'Saving…' : 'Save and continue'}
        </button>
        <button
          onClick={() => setDismissed(true)}
          className="mt-3 min-h-11 w-full text-xs font-semibold text-ink-3 transition-colors hover:text-ink"
        >
          Skip for now — statements won’t import until this is filled in
        </button>
      </div>
    </div>
  )
}
