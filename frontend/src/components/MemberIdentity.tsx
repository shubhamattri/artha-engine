import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchMembers, updateMemberIdentity, type MemberRow } from '../api/client'
import { Card, Icon, Loading, SectionHeader } from './ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

/**
 * Identity a bank needs before it will open a statement.
 *
 * What each field actually buys is stated on the field itself, because "we need your PAN" with no
 * reason reads as data harvesting. Measured on the real mailbox: date of birth alone unlocked 23 of
 * 23 card statements, so it is the only one that blocks. The rest are offered with the specific thing
 * they unlock, and skipping them costs exactly that and nothing else.
 *
 * Values are write-only. The server returns whether a field is set, never what it holds, so a PAN or
 * mobile never lands in a browser cache, a screenshot, or a support transcript.
 */
type FieldKey = 'dob' | 'mobile' | 'pan' | 'pincode'

// Shape only. WHY each field is asked for comes from the server, because the profile screen shows
// the same sentences and two copies of a sentence become two different sentences.
const FIELDS: {
  key: FieldKey
  label: string
  required?: boolean
  type?: string
  placeholder: string
  inputMode?: 'numeric' | 'text'
  maxLength?: number
}[] = [
  { key: 'dob', label: 'Date of birth', required: true, type: 'date', placeholder: '' },
  { key: 'mobile', label: 'Mobile number', placeholder: '9876543210', inputMode: 'numeric', maxLength: 10 },
  { key: 'pan', label: 'PAN', placeholder: 'ABCDE1234F', maxLength: 10 },
  { key: 'pincode', label: 'PIN code', placeholder: '560001', inputMode: 'numeric', maxLength: 6 },
]

function PersonForm({ m, unlocks }: { m: MemberRow; unlocks: Record<string, string> }) {
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('household.admin', 'edit')
  const [form, setForm] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)

  const has = (k: FieldKey) =>
    ({ dob: m.has_dob, mobile: m.has_mobile, pan: m.has_pan, pincode: m.has_pincode }[k])

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, string> = {}
      // only what was typed, so a blank field never wipes a stored value
      for (const [k, v] of Object.entries(form)) if (v?.trim()) body[k] = v.trim()
      return updateMemberIdentity(m.id, body)
    },
    onSuccess: () => { setForm({}); setSaved(true); refresh() },
  })

  const dirty = Object.values(form).some((v) => v?.trim())

  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <div className="text-base font-semibold text-ink">{m.name}</div>
        {has('dob') ? (
          <span className="flex items-center gap-1.5 text-xs font-semibold text-positive">
            <Icon name="check" size={14} /> Statements can be opened
          </span>
        ) : (
          <span className="rounded-chip bg-warn/15 px-2 py-1 text-[11px] font-bold text-warn">
            Date of birth needed
          </span>
        )}
      </div>

      {!mayEdit ? (
        <div className="mt-5"><ViewOnly what={`${m.name}’s statement access`} /></div>
      ) : (
      <>
      <div className="mt-5 space-y-5">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label htmlFor={`${m.id}-${f.key}`}
              className="flex items-baseline gap-2 text-sm font-medium text-ink">
              {f.label}
              {f.required
                ? <span className="text-[11px] font-semibold text-accent">required</span>
                : <span className="text-[11px] text-ink-3">optional</span>}
              {has(f.key) && <span className="text-[11px] font-semibold text-positive">saved</span>}
            </label>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{unlocks[f.key]}</p>
            <input
              id={`${m.id}-${f.key}`}
              type={f.type ?? 'text'}
              inputMode={f.inputMode}
              maxLength={f.maxLength}
              value={form[f.key] ?? ''}
              placeholder={has(f.key) ? 'Saved. Type to replace.' : f.placeholder}
              onChange={(e) => setForm({
                ...form,
                [f.key]: f.key === 'pan' ? e.target.value.toUpperCase() : e.target.value,
              })}
              className="mt-2 min-h-11 w-full rounded-input border border-line bg-surface-2 px-3 text-base text-ink outline-none transition-colors placeholder:text-ink-3 focus:border-accent"
            />
          </div>
        ))}
      </div>

      {save.isError && (
        <div className="mt-4 flex items-start gap-2 text-sm font-medium text-negative">
          <Icon name="alert" size={16} />
          <span>
            {(save.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
              ?? 'Could not save. Check the values and try again.'}
          </span>
        </div>
      )}
      {saved && !save.isError && !dirty && (
        <div className="mt-4 flex items-center gap-2 text-sm font-medium text-positive">
          <Icon name="check" size={16} /> Saved.
        </div>
      )}

      <button
        onClick={() => save.mutate()}
        disabled={!dirty || save.isPending}
        className="mt-6 min-h-11 w-full rounded-input bg-accent px-4 text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-40"
      >
        {save.isPending ? 'Saving…' : `Save ${m.name}’s details`}
      </button>
      </>
      )}
    </Card>
  )
}

export default function MemberIdentity() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['members'], queryFn: fetchMembers })

  if (isLoading) return <Loading />
  if (isError || !data) {
    return (
      <Card>
        <div className="text-sm font-medium text-negative">Could not load the household.</div>
        <button onClick={() => refetch()}
          className="mt-3 min-h-11 rounded-input border border-line px-4 text-sm font-semibold text-ink-2 hover:border-accent hover:text-accent">
          Try again
        </button>
      </Card>
    )
  }

  const people = data.members.filter((m) => !m.is_household)

  return (
    <div className="space-y-4">
      <SectionHeader
        level={2}
        title="Statement access"
        sub="Banks lock statement PDFs with a password built from these. Stored on your own server, never shown again once saved."
      />
      {people.map((m) => <PersonForm key={m.id} m={m} unlocks={data.unlocks ?? {}} />)}
    </div>
  )
}
