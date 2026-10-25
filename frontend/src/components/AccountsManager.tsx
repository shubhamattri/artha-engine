import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createAccount, deleteAccount, discoverStatementSchedule, fetchAccounts, fetchUnknownSenders,
  setStatementSchedule, updateAccount,
  type AccountRow, type StatementSender, type UnknownSender,
} from '../api/client'
import {
  AddPanel, Card, EmptyState, ErrorState, Field, FormRow, HolderOptions, Loading, RecordRow,
  SectionHeader, SelectInput, SubmitButton, TextInput, inputClass, useConfirm,
} from './ui'
import SendersTriage from './SendersTriage'
import { ViewOnly, useCan } from '../auth/Can'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'

type FormState = { bank: string; kind: 'card' | 'account'; last4: string; holder: string; label: string; sms_senders: string }
const BLANK: FormState = { bank: '', kind: 'card', last4: '', holder: '', label: '', sms_senders: '' }

/** The form used for both add and edit — one definition, so the two can never drift apart. */
function AccountForm({ value, onChange, onSubmit, busy, submitLabel }: {
  value: FormState
  onChange: (v: FormState) => void
  onSubmit: () => void
  busy: boolean
  submitLabel: string
}) {
  const set = (k: keyof FormState, v: string) => onChange({ ...value, [k]: v } as FormState)
  return (
    <FormRow submit={<SubmitButton disabled={busy || !value.bank.trim() || value.last4.length < 4}
      onClick={onSubmit}>{submitLabel}</SubmitButton>}>
      <Field label="Bank">
        <TextInput value={value.bank} onChange={(e) => set('bank', e.target.value)} placeholder="e.g. HDFC" />
      </Field>
      <Field label="Type">
        <SelectInput value={value.kind} onChange={(e) => set('kind', e.target.value)}>
          <option value="card">Credit card</option>
          <option value="account">Bank account</option>
        </SelectInput>
      </Field>
      <Field label="Last 4 digits">
        {/* Only the last 4 are ever stored — paste a full number and it is truncated server-side too. */}
        <TextInput value={value.last4} inputMode="numeric" maxLength={4}
          onChange={(e) => set('last4', e.target.value.replace(/\D/g, '').slice(-4))} placeholder="1234" />
      </Field>
      <Field label="Holder">
        <select value={value.holder} onChange={(e) => set('holder', e.target.value)} className={inputClass}>
          <option value="">—</option>
          <HolderOptions />
        </select>
      </Field>
      <Field label="Label (optional)">
        <TextInput value={value.label} onChange={(e) => set('label', e.target.value)} placeholder="e.g. salary" />
      </Field>
      <Field label="SMS senders" hint="Comma-separated, e.g. HDFCBK. This is what makes alerts get captured.">
        <TextInput value={value.sms_senders} onChange={(e) => set('sms_senders', e.target.value.toUpperCase())}
          placeholder="HDFCBK,HDFCCC" />
      </Field>
    </FormRow>
  )
}

function ordinal(n: number): string {
  const teens = n % 100
  if (teens >= 11 && teens <= 13) return `${n}th`
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`
}

/** The one sentence describing an account's schedule, in the same words on the row and in the panel. */
function scheduleLine(a: AccountRow): string | null {
  if (!a.statement_day) return null
  const from = a.statement_sender ? ` from ${a.statement_sender}` : ''
  const run = a.statement_run_day ? `, and we go looking on the ${ordinal(a.statement_run_day)}` : ''
  return `The statement${from} lands on the ${ordinal(a.statement_day)}${run}.`
}

/**
 * Where the statement schedule gets set, per account.
 *
 * The scan reads the mailbox, which is slow and is nobody's idea of a page load — so it only runs
 * when asked. Senders whose date wobbles are shown WITH the wobble rather than filtered out: the
 * mailbox found them, and "we looked and it moves around" is a better answer than a short list that
 * reads as nothing being there.
 */
function StatementSchedule({ account, onApplied }: {
  account: AccountRow
  onApplied: () => void
}) {
  const [asked, setAsked] = useState(false)
  const found = useQuery({
    queryKey: ['statement-schedule-discover'],
    queryFn: () => discoverStatementSchedule(190),
    enabled: asked,
    retry: false,
    // One scan serves every account on this page — expanding the next row shouldn't re-read the mailbox.
    staleTime: 5 * 60 * 1000,
  })
  // The response carries only the schedule fields, so the row is refetched rather than patched.
  const apply = useMutation({
    mutationFn: (s: StatementSender) => setStatementSchedule(account.id, {
      statement_sender: s.sender, statement_day: s.day, statement_run_day: s.run_day,
    }),
    onSuccess: onApplied,
  })

  const current = scheduleLine(account)
  const senders = found.data?.senders ?? []

  return (
    <div className="mt-2 rounded-2xl border border-line bg-surface-2 p-4">
      <div className="tracked text-[10px] font-semibold text-ink-3">Statement schedule</div>
      <p className="mt-1 text-[11.5px] leading-relaxed text-ink-2">
        {current ?? 'Not set, so nothing fetches this account’s statement on its own.'}
        {account.statement_last_imported &&
          ` Last imported ${account.statement_last_imported.slice(0, 10)}.`}
      </p>

      {!asked && (
        <button onClick={() => setAsked(true)}
          className="mt-3 inline-flex min-h-11 items-center rounded-input border border-accent px-3 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft">
          Find it from my email
        </button>
      )}

      {asked && found.isLoading && <div className="mt-3"><Loading rows={2} /></div>}

      {found.data && !found.data.available && (
        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-2">
          Your email can’t be read from here, so the date can’t be worked out.
          {found.data.reason ? ` ${found.data.reason}` : ''}
        </p>
      )}

      {found.data?.available && senders.length === 0 && (
        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-2">
          Nothing statement-shaped in the last six months of email.
        </p>
      )}

      {senders.length > 0 && (
        <div className="mt-3">
          <p className="text-[11.5px] leading-relaxed text-ink-2">
            Every sender that mails something statement-shaped, most likely first. Pick the one that
            mails this account.
          </p>
          <div className="mt-1">
            {senders.map((s) => (
              <div key={`${s.sender}|${s.shape}`}
                className="flex items-start justify-between gap-3 border-t border-line px-1 py-2.5 first:border-0">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-ink">{s.sender}</div>
                  <div className="mt-0.5 truncate text-[11px] text-ink-3">{s.subject}</div>
                  <div className="money mt-0.5 text-[11px] tabular-nums text-ink-3">
                    {s.stable
                      ? `${ordinal(s.day)} of the month`
                      : `around the ${ordinal(s.day)}, moves by up to ${s.spread} day${s.spread === 1 ? '' : 's'}`}
                    {' · '}{s.samples} mail{s.samples === 1 ? '' : 's'} · last {s.last_seen.slice(0, 10)}
                  </div>
                </div>
                <button onClick={() => apply.mutate(s)} disabled={apply.isPending}
                  className="min-h-11 shrink-0 rounded-input border border-line px-3 text-xs font-semibold text-ink-2 transition-colors hover:border-accent hover:text-accent disabled:opacity-50">
                  Use this
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {apply.isError && (
        <div className="mt-2 text-xs font-medium text-negative">
          {(apply.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            ?? 'Could not save that schedule.'}
        </div>
      )}
    </div>
  )
}

export default function AccountsManager() {
  const refresh = useHouseholdRefresh()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const mayEdit = useCan('ledger.import', 'edit')
  const [form, setForm] = useState({ ...BLANK })
  const [editing, setEditing] = useState<number | null>(null)
  const [editForm, setEditForm] = useState({ ...BLANK })
  const [scheduling, setScheduling] = useState<number | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['accounts'], queryFn: fetchAccounts })
  const senders = useQuery({ queryKey: ['unknown-senders'], queryFn: fetchUnknownSenders })

  const done = () => { refresh(); setForm({ ...BLANK }); setEditing(null) }
  const add = useMutation({ mutationFn: createAccount, onSuccess: done })
  const edit = useMutation({
    mutationFn: (v: { id: number; body: Partial<AccountRow> }) => updateAccount(v.id, v.body),
    onSuccess: done,
  })
  const del = useMutation({ mutationFn: (id: number) => deleteAccount(id), onSuccess: refresh })

  if (isLoading) return <Loading />
  // A loading-or-missing guard swallows isError, so a failed fetch spins this skeleton forever
  // rather than saying it broke. States.tsx:5 warns about exactly this shape.
  if (isError || !data) return <ErrorState message="Couldn’t load your accounts." onRetry={() => refetch()} />
  const accounts = data.accounts ?? []
  const cards = accounts.filter((a) => a.kind === 'card')
  const banks = accounts.filter((a) => a.kind === 'account')

  /** Pre-fill the add form from an unrecognised sender — the whole point of the inbox. */
  const claim = (u: UnknownSender) => {
    setForm({ ...BLANK, bank: u.suggested_token, sms_senders: u.suggested_token })
    document.getElementById('accounts-add')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const row = (a: AccountRow) => (
    <div key={a.id}>
      {editing === a.id ? (
        <Card>
          <AccountForm value={editForm} onChange={setEditForm} busy={edit.isPending}
            submitLabel="Save" onSubmit={() => edit.mutate({ id: a.id, body: editForm })} />
          <button onClick={() => setEditing(null)}
            className="mt-2 text-xs font-semibold text-ink-3 hover:text-ink">Cancel</button>
        </Card>
      ) : (
        <RecordRow
          title={`${a.bank} ••${a.last4}`}
          subtitle={[a.label, a.holder].filter(Boolean).join(' · ') || undefined}
          fields={[
            { label: 'Type', value: a.kind === 'card' ? 'Credit card' : 'Bank account' },
            // Without a sender the account exists but no alert will ever be captured for it.
            { label: 'SMS senders', value: a.sms_senders || 'none — alerts not tracked' },
            { label: 'Statement', value: a.statement_day ? ordinal(a.statement_day) : 'not set' },
          ]}
          actions={!mayEdit ? undefined : (
            <>
              <button
                onClick={() => setScheduling((id) => (id === a.id ? null : a.id))}
                className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent"
              >
                {scheduling === a.id ? 'Done' : 'Statement'}
              </button>
              <button
                onClick={() => {
                  setEditing(a.id)
                  setEditForm({
                    bank: a.bank, kind: a.kind, last4: a.last4, holder: a.holder ?? '',
                    label: a.label ?? '', sms_senders: a.sms_senders ?? '',
                  })
                }}
                className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-accent"
              >
                Edit
              </button>
              <button
                onClick={async () => {
                  if (await confirm({
                    title: `Remove ${a.bank} ••${a.last4}?`,
                    message: 'Transactions already imported are kept. New alerts from this account stop being captured.',
                    confirmLabel: 'Remove',
                    danger: true,
                  })) del.mutate(a.id)
                }}
                className="min-h-11 px-2 text-xs font-semibold tracked text-ink-3 transition-colors hover:text-negative"
              >
                Delete
              </button>
            </>
          )}
        />
      )}
      {mayEdit && scheduling === a.id && editing !== a.id && (
        <StatementSchedule account={a} onApplied={() => {
          qc.invalidateQueries({ queryKey: ['accounts'] })
          setScheduling(null)
        }} />
      )}
    </div>
  )

  const unknown = senders.data?.unknown ?? []

  return (
    <div className="space-y-6">
      {/* The inbox goes FIRST: it is the thing that tells you something is missing. Burying it under
          the list of accounts you already know about defeats the purpose. */}
      {unknown.length > 0 && <SendersTriage unknown={unknown} onClaim={claim} />}

      {/* The list below still shows every account in full; what goes is the form, and with it the
          Edit and Delete on each row. One sentence, not a panel of fields that cannot be submitted. */}
      {!mayEdit ? <ViewOnly what="your accounts and cards" /> : (
      <div id="accounts-add">
        <AddPanel label="Add account or card">
          <AccountForm value={form} onChange={setForm} busy={add.isPending}
            submitLabel="Add" onSubmit={() => add.mutate(form)} />
          {add.isError && (
            <div className="mt-2 text-xs font-medium text-negative">
              {(add.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Could not add that.'}
            </div>
          )}
        </AddPanel>
      </div>
      )}

      {accounts.length === 0 ? (
        <EmptyState title="No accounts yet"
          hint="Add a bank account or credit card so its alerts are captured and its spending shows up here." />
      ) : (
        <>
          {cards.length > 0 && (
            <div className="space-y-2">
              <SectionHeader level={2} title="Credit cards" sub={`${cards.length}`} />
              {cards.map(row)}
            </div>
          )}
          {banks.length > 0 && (
            <div className="space-y-2">
              <SectionHeader level={2} title="Bank accounts" sub={`${banks.length}`} />
              {banks.map(row)}
            </div>
          )}
        </>
      )}

      {senders.data && !senders.data.available && (
        <div className="text-[11px] text-ink-3">
          Messages can’t be read on this machine, so unrecognised senders can’t be listed. Everything
          else on this page still works.
        </div>
      )}
    </div>
  )
}
