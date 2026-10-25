import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  updateProfile, useHouseholdRoster, useProfile,
  type IdentityKey, type Profile as ProfileData, type ProfilePatch,
} from '../api/profile'
import ChangePassword from '../components/ChangePassword'
import {
  Card, Chip, EmptyState, ErrorState, Field, FormRow, Icon, Loading, PageContainer, SectionHeader,
  SubmitButton, TextInput, inputClass,
} from '../components/ui'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'

/**
 * Profile — the one person who is signed in, and the household around them.
 *
 * Built on `/api/profile`, which resolves the person from the session and creates the missing link if
 * a login was never joined to a member. The screen used to read `/api/members`, which returns a
 * household and cannot say which row is you, so it matched names and called the result identity.
 *
 * "Complete" here means the REQUIRED field is filled — date of birth, and nothing else. `/members`
 * uses the word for all four fields, which is a different question, so the two are never mixed on
 * this screen: every field says required or optional and the copy stops there.
 */
type FieldInput = {
  label: string
  type?: string
  placeholder: string
  inputMode?: 'numeric' | 'text'
  maxLength?: number
}

// Labels and keyboards only. What a field UNLOCKS is the server's copy and is rendered from the
// response, so the reason a household is asked for a PAN can never drift from the code that wants it.
const INPUT: Record<IdentityKey, FieldInput> = {
  dob: { label: 'Date of birth', type: 'date', placeholder: '' },
  mobile: { label: 'Mobile number', placeholder: '0000000000', inputMode: 'numeric', maxLength: 10 },
  pan: { label: 'PAN', placeholder: 'ABCDE1234F', maxLength: 10 },
  pincode: { label: 'PIN code', placeholder: '000000', inputMode: 'numeric', maxLength: 6 },
}

const labels = (keys: IdentityKey[]) => keys.map((k) => INPUT[k].label).join(', ')

const detail = (e: unknown): string | undefined =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail

export default function Profile() {
  const { data, isLoading, isError, refetch } = useProfile()

  return (
    <PageContainer>
      <SectionHeader
        title="Profile"
        eyebrow={data?.household?.name}
        sub="Who you are here, what your banks need from you, and who else is in this household."
      />

      {isLoading ? (
        <Loading />
      ) : isError || !data ? (
        <ErrorState message="Couldn’t load your profile." onRetry={() => refetch()} />
      ) : (
        <>
          <SignedInAs p={data} />
          <YourDetails p={data} />
          <YourAccounts p={data} />
          <Household />
          <ChangePassword />
        </>
      )}
    </PageContainer>
  )
}

function SignedInAs({ p }: { p: ProfileData }) {
  return (
    <Card>
      <div className="flex items-center gap-3.5">
        <span className="flex shrink-0 items-center justify-center rounded-full bg-accent-soft text-lg font-bold text-accent"
              style={{ width: 56, height: 56 }} aria-hidden>
          {(p.name || p.email || '?').trim().charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0">
          <div className="tracked text-[10px] font-semibold text-ink-3">Signed in as</div>
          <div className="truncate font-semibold text-ink" style={{ fontSize: 19 }}>{p.name}</div>
          <div className="truncate text-xs text-ink-3">{p.email} · {p.username}</div>
        </div>
      </div>
    </Card>
  )
}

/**
 * Your own identity, edited through PATCH /profile.
 *
 * Self-only and self-service: the server takes no target parameter and allows it for anyone signed
 * in, so there is no permission to gate on. Values are write-only — the response says whether a
 * field is set, never what it holds, which is why every input starts blank however much is stored.
 */
function YourDetails({ p }: { p: ProfileData }) {
  const qc = useQueryClient()
  const refresh = useHouseholdRefresh()
  const [name, setName] = useState(p.name)
  const [typed, setTyped] = useState<Partial<Record<IdentityKey, string>>>({})
  const [cleared, setCleared] = useState<IdentityKey[]>([])
  const [saved, setSaved] = useState(false)

  useEffect(() => { setName(p.name) }, [p.name])

  const keys = Object.keys(p.identity) as IdentityKey[]
  const renamed = name.trim() !== '' && name.trim() !== p.name
  const dirty = renamed || cleared.length > 0 || keys.some((k) => typed[k]?.trim())

  const save = useMutation({
    mutationFn: () => {
      const body: ProfilePatch = {}
      if (renamed) body.name = name.trim()
      for (const k of keys) {
        const v = typed[k]?.trim()
        // An omitted key leaves a stored value alone, so "" is the only way to erase one.
        if (v) body[k] = v
        else if (cleared.includes(k)) body[k] = ''
      }
      return updateProfile(body)
    },
    onSuccess: () => {
      setTyped({}); setCleared([]); setSaved(true)
      for (const key of [['profile'], ['profile-household'], ['members'], ['auth', 'me']]) {
        qc.invalidateQueries({ queryKey: key })
      }
      refresh()
    },
  })

  const toggleClear = (k: IdentityKey) => {
    setSaved(false)
    setTyped({ ...typed, [k]: '' })
    setCleared(cleared.includes(k) ? cleared.filter((c) => c !== k) : [...cleared, k])
  }

  return (
    <div className="space-y-4">
      <SectionHeader
        level={2}
        title="Your details"
        sub="Banks lock statement PDFs with a password built from these. Stored on your own server and never shown again once saved — the app only remembers that it has them."
      />

      <Card>
        <FormRow
          cols={2}
          onSubmit={(e) => { e.preventDefault(); setSaved(false); save.mutate() }}
          submit={<SubmitButton disabled={!dirty || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save your details'}
          </SubmitButton>}
        >
          <TextInput
            label="Name"
            hint="What this app calls you, and how a card or account holder is matched to you."
            value={name}
            onChange={(e) => { setSaved(false); setName(e.target.value) }}
          />

          {keys.map((k) => {
            const f = p.identity[k]
            const id = `identity-${k}`
            const isCleared = cleared.includes(k)
            return (
              <div key={k}>
                <Field
                  label={`${INPUT[k].label} · ${f.required ? 'required' : 'optional'}`}
                  hint={f.unlocks}
                  htmlFor={id}
                >
                  <input
                    id={id}
                    className={inputClass}
                    type={INPUT[k].type ?? 'text'}
                    inputMode={INPUT[k].inputMode}
                    maxLength={INPUT[k].maxLength}
                    value={typed[k] ?? ''}
                    placeholder={f.set ? 'Saved. Type to replace.' : INPUT[k].placeholder}
                    onChange={(e) => {
                      setSaved(false)
                      setCleared(cleared.filter((c) => c !== k))
                      const v = e.target.value
                      setTyped({ ...typed, [k]: k === 'pan' ? v.toUpperCase() : v })
                    }}
                  />
                </Field>
                {f.set && (
                  <div className="mt-1.5 flex items-center gap-3 text-[11px]">
                    {isCleared ? (
                      <span className="font-semibold text-warn">Will be erased when you save</span>
                    ) : (
                      <span className="flex items-center gap-1 font-semibold text-positive">
                        <Icon name="check" size={12} /> Saved
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => toggleClear(k)}
                      className="font-semibold text-ink-3 underline underline-offset-2 hover:text-accent"
                    >
                      {isCleared ? 'Keep it' : 'Remove it'}
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </FormRow>

        {save.isError && (
          <div className="mt-4 flex items-start gap-2 text-sm font-medium text-negative">
            <Icon name="alert" size={16} />
            <span>{detail(save.error) ?? 'Could not save. Check the values and try again.'}</span>
          </div>
        )}
        {saved && !save.isError && !dirty && (
          <div className="mt-4 flex items-center gap-2 text-sm font-medium text-positive">
            <Icon name="check" size={16} /> Saved.
          </div>
        )}

        {p.missing_required.length > 0 && (
          <p className="mt-4 text-xs leading-relaxed text-warn">
            {labels(p.missing_required)} is what every statement password is built from. Until it is
            filled in, nothing in your name can be opened or reconciled.
          </p>
        )}
      </Card>
    </div>
  )
}

/** What this person holds, so the form above is asking for details against something visible. */
function YourAccounts({ p }: { p: ProfileData }) {
  return (
    <div className="space-y-4">
      <SectionHeader
        level={2}
        title="In your name"
        sub="The cards and accounts filed under you. These are the statements your details above open."
      />

      {p.accounts.length === 0 ? (
        <EmptyState
          icon="card"
          title="Nothing is filed under you yet"
          hint={`Accounts are matched to the holder name “${p.holder_key}”. Set a holder on a card or account and it will appear here.`}
        />
      ) : (
        <Card>
          {p.accounts.map((a) => (
            <div key={a.id}
                 className="flex items-center justify-between gap-3 border-b border-line py-3 first:pt-0 last:border-0 last:pb-0">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-ink">{a.bank} ••{a.last4}</div>
                {a.label && <div className="truncate text-xs text-ink-3">{a.label}</div>}
              </div>
              <Chip>{a.kind === 'card' ? 'Credit card' : 'Bank account'}</Chip>
            </div>
          ))}
        </Card>
      )}
    </div>
  )
}

/**
 * Everyone else, read-only.
 *
 * A household is not one person: a card in someone else's name is locked with THEIR date of birth,
 * so "whose statements cannot be opened" is a question about them, not about you — and nothing else
 * in the app answers it.
 */
function Household() {
  const { data, isLoading, isError, refetch } = useHouseholdRoster()

  if (isLoading) return <Loading rows={2} />
  if (isError || !data) {
    return <ErrorState message="Couldn’t load who else is in this household." onRetry={() => refetch()} />
  }

  const others = data.people.filter((m) => !m.is_you)

  return (
    <div className="space-y-4">
      <SectionHeader
        level={2}
        title="Everyone else here"
        sub="Their statements are locked with their own details, so only they can fill these in. This is what is still missing."
      />

      {others.length === 0 ? (
        <EmptyState
          icon="list"
          title="You are the only person in this household"
          hint="Add someone and their statements can be reconciled here too."
        />
      ) : (
        <Card>
          {others.map((m) => (
            <div key={m.member_id}
                 className="flex items-center justify-between gap-3 border-b border-line py-3 first:pt-0 last:border-0 last:pb-0">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-ink">{m.name}</div>
                <div className="mt-0.5 truncate text-xs text-ink-3">
                  {m.has_login ? 'Can sign in' : 'No login'}
                  {m.missing_optional.length > 0 && ` · no ${labels(m.missing_optional)}`}
                </div>
              </div>
              {m.missing_required.length === 0 ? (
                <span className="flex shrink-0 items-center gap-1 text-xs font-semibold text-positive">
                  <Icon name="check" size={14} /> Statements open
                </span>
              ) : (
                <span className="shrink-0 rounded-chip bg-warn/15 px-2 py-0.5 text-[11px] font-bold text-warn">
                  {labels(m.missing_required)} needed
                </span>
              )}
            </div>
          ))}
        </Card>
      )}
    </div>
  )
}
