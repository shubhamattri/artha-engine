import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  uploadStatement, gmailStatus, gmailSync, gmailSyncAlerts, syncSms, fetchAccounts, fetchSupported,
} from '../api/client'
import { Card, Icon, SelectInput, TextInput, FormRow, SubmitButton } from './ui'
import { ViewOnly, useCan } from '../auth/Can'

// Banks come from the household's registered accounts, not a list in source. There is deliberately
// no fallback list: a household with nothing registered yet used to be shown four Indian banks and
// had to pick one of them even when it banked elsewhere, which is the same wrong answer for everyone.
// With none registered the field becomes free text below, so any bank can be named.
const CARD_TYPES = ['Credit Card', 'Account']

type Result = {
  id: number
  file: File
  name: string
  status: 'uploading' | 'done' | 'error'
  detail?: string
  confidence?: string | null   // detection confidence on success
  bank?: string
  last4?: string
}

let _seq = 0

/**
 * What the parsers can actually read, answered before a file is dragged in rather than after it
 * fails. Collapsed by default — this is a reference, not the job the screen is here to do.
 */
function SupportedFormats() {
  const [open, setOpen] = useState(false)
  // The parser registry only changes when the app is rebuilt, so one fetch per session is plenty.
  const { data } = useQuery({ queryKey: ['supported'], queryFn: fetchSupported, staleTime: Infinity, retry: false })
  if (!data) return null

  return (
    <div className="mt-2 border-t border-line pt-2">
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="inline-flex min-h-11 items-center gap-1.5 text-[11px] font-semibold text-ink-3 transition-colors hover:text-ink">
        <Icon name="chevron" size={12} className={open ? 'rotate-90' : ''} />
        Reads {data.accepted_file_types.join(' ')} · which banks?
      </button>
      {open && (
        <div className="mt-1 space-y-1 pb-1">
          {data.banks.map((b) => (
            <div key={`${b.bank}|${b.statement}`} className="flex items-baseline justify-between gap-3 text-[11px]">
              <span className="min-w-0 truncate text-ink-2">{b.bank} · {b.statement}</span>
              <span className="money shrink-0 tabular-nums text-ink-3">{b.accepts.join(' ')}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** The household's own banks. react-query dedupes, so both components can call this freely. */
function useBanks(): string[] {
  const { data } = useQuery({ queryKey: ['accounts'], queryFn: fetchAccounts, retry: false })
  const registered = [...new Set((data?.accounts ?? []).map((a) => a.bank).filter(Boolean))]
  return registered.sort()
}

export default function UploadStatements() {
  const mayEdit = useCan('ledger.import', 'edit')
  const qc = useQueryClient()
  const gmailStatusQuery = useQuery({ queryKey: ['gmail-status'], queryFn: gmailStatus, retry: false })
  const banks = useBanks()
  const inputRef = useRef<HTMLInputElement>(null)
  const [results, setResults] = useState<Result[]>([])
  const [dragging, setDragging] = useState(false)
  const [gmail, setGmail] = useState<{ busy: boolean; msg?: string }>({ busy: false })

  // Nothing in this card is a read: the Gmail pull, the dropzone and the re-parse form all write
  // to the ledger. So the card becomes the sentence rather than keeping a shell with dead controls.
  if (!mayEdit) return <ViewOnly what="the statements already imported" />

  async function syncGmail() {
    if (gmailStatusQuery.data && !gmailStatusQuery.data.connected) {
      setGmail({ busy: false, msg: 'Gmail is not connected on the server yet.' })
      return
    }
    setGmail({ busy: true })
    try {
      const r = await gmailSync()
      const alerts = await gmailSyncAlerts(21).catch(() => null)  // bank alert emails
      const sms = await syncSms(30).catch(() => null)             // bank SMS via Mac Messages
      const extra = [
        alerts?.added ? `${alerts.added} from card alerts` : '',
        sms?.added ? `${sms.added} from SMS` : '',
      ].filter(Boolean).join(' · ')
      setGmail({ busy: false, msg: `Synced: ${r.fetched} statement(s) · ${r.transactions_new} new${extra ? ' · ' + extra : ''}` })
      ;['gmail-status', 'txns', 'txn-facets', 'budget', 'cashflow', 'cockpit'].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] }))
    } catch (e: any) {
      setGmail({ busy: false, msg: e?.response?.data?.detail ?? 'Gmail sync failed' })
    }
  }

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['txns'] })
    qc.invalidateQueries({ queryKey: ['txn-facets'] })
    qc.invalidateQueries({ queryKey: ['budget'] })
    qc.invalidateQueries({ queryKey: ['cashflow'] })
  }

  function patch(id: number, p: Partial<Result>) {
    setResults((r) => r.map((x) => (x.id === id ? { ...x, ...p } : x)))
  }

  // One upload. override = explicit {bank, card_type, card_last4} for the manual re-parse path.
  async function send(rec: Result, override?: { bank: string; card_type: string; card_last4: string }) {
    patch(rec.id, { status: 'uploading', detail: undefined })
    const fd = new FormData()
    fd.append('file', rec.file)
    if (override) {
      fd.append('bank', override.bank)
      fd.append('card_type', override.card_type)
      fd.append('card_last4', override.card_last4)
    }
    try {
      const res: any = await uploadStatement(fd)
      patch(rec.id, {
        status: 'done',
        confidence: res.auto_detected ? res.detect_confidence : 'manual',
        bank: res.bank, last4: res.card_last4,
        detail: `${res.bank?.toUpperCase?.() ?? '?'} · ${res.card_type} ••${res.card_last4 ?? '?'} · ${res.stmt_month} — ${res.transactions_new} new / ${res.transactions_parsed} parsed`,
      })
      invalidate()
    } catch (e: any) {
      patch(rec.id, { status: 'error', detail: e?.response?.data?.detail ?? 'Upload failed' })
    }
  }

  function handleFiles(files: FileList | File[]) {
    const list = Array.from(files)
    if (!list.length) return
    const recs: Result[] = list.map((f) => ({ id: ++_seq, file: f, name: f.name, status: 'uploading' }))
    setResults((r) => [...recs, ...r])
    recs.forEach((rec) => send(rec))
  }

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <span className="tracked text-[11px] text-ink-3">Add statements</span>
        <button onClick={syncGmail} disabled={gmail.busy || gmailStatusQuery.isLoading || gmailStatusQuery.data?.connected === false}
          className="min-h-11 rounded-input border border-line px-3 text-xs font-semibold text-ink-2 transition-colors hover:border-line-2 hover:text-ink disabled:opacity-50">
          {gmail.busy ? 'Syncing Gmail…' : gmailStatusQuery.data?.connected === false ? 'Gmail not connected' : 'Sync from Gmail'}
        </button>
      </div>
      {gmailStatusQuery.data?.connected === false && (
        <div className="mb-2 text-[11px] text-ink-3">
          Gmail sync needs one-time read-only authorization on the server.
        </div>
      )}
      {gmail.msg && <div className="mb-2 text-[11px] text-ink-3">{gmail.msg}</div>}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files) }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-xl border border-dashed px-6 py-7 text-center transition-colors ${dragging ? 'border-accent bg-accent-soft' : 'border-line-2 hover:border-accent/60'}`}
      >
        <div className="text-sm text-ink-2">
          Drop credit-card or bank statements here, or <span className="text-accent">browse</span>
        </div>
        <div className="mt-1 tracked text-[11px] text-ink-3">
          PDF / CSV / Excel · multiple at once · we auto-detect the bank, card and month
        </div>
        <input ref={inputRef} type="file" multiple accept=".pdf,.csv,.xls,.xlsx" className="hidden"
          onChange={(e) => e.target.files && handleFiles(e.target.files)} />
      </div>

      <SupportedFormats />

      {results.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {results.map((r) => {
            const needsOverride = r.status === 'error' || (r.status === 'done' && (r.confidence === 'low' || r.confidence === 'medium'))
            return (
              <div key={r.id} className="text-xs">
                <div className="flex items-start gap-2">
                  <span className={r.status === 'done' ? 'text-positive' : r.status === 'error' ? 'text-negative' : 'text-ink-3'}>
                    {r.status === 'done' ? '✓' : r.status === 'error' ? '✕' : '…'}
                  </span>
                  <span className="text-ink-2">{r.name}</span>
                  {r.detail && <span className={r.status === 'error' ? 'text-negative' : 'text-ink-3'}>— {r.detail}</span>}
                </div>
                {needsOverride && <Override rec={r} onRetry={(o) => send(r, o)} />}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

function Override({ rec, onRetry }: { rec: Result; onRetry: (o: { bank: string; card_type: string; card_last4: string }) => void }) {
  const banks = useBanks()
  // Defaulting to a named bank silently pre-answered this for every household. Fall back to
  // whatever the household actually registered, and to blank when it has registered nothing.
  const [bank, setBank] = useState(rec.bank ? rec.bank.toUpperCase() : '')
  const [cardType, setCardType] = useState('Credit Card')
  const [last4, setLast4] = useState(rec.last4 ?? '')
  return (
    <div className="mt-2">
      <p className="mb-2 text-[11px] text-ink-3">
        {rec.status === 'error' ? "couldn't auto-detect — set it:" : 'wrong? override:'}
      </p>
      <FormRow
        cols={3}
        onSubmit={(e) => { e.preventDefault(); onRetry({ bank, card_type: cardType, card_last4: last4 }) }}
        submit={<SubmitButton type="submit">Re-parse</SubmitButton>}
      >
        {banks.length > 0 ? (
          <SelectInput label="Bank" value={bank} onChange={(e) => setBank(e.target.value)}>
            <option value="">Select</option>
            {banks.map((b) => <option key={b}>{b}</option>)}
          </SelectInput>
        ) : (
          <TextInput label="Bank" placeholder="your bank" value={bank}
            onChange={(e) => setBank(e.target.value.toUpperCase())} />
        )}
        <SelectInput label="Card type" value={cardType} onChange={(e) => setCardType(e.target.value)}>
          {CARD_TYPES.map((t) => <option key={t}>{t}</option>)}
        </SelectInput>
        <TextInput label="Last 4" placeholder="last4" value={last4} onChange={(e) => setLast4(e.target.value)} />
      </FormRow>
    </div>
  )
}
