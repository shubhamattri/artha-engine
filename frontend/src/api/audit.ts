import { api } from './client'

/**
 * The audit trail: what changed, who changed it, and putting it back.
 *
 * The log is append-only. Undoing a change does not remove its event — it writes a new one — so the
 * history can always answer "why is this number what it is", including when the answer is "somebody
 * changed it and then changed it back".
 */
export interface AuditEvent {
  id: number
  /** Null once a transaction has been hard-deleted. The event outlives the row on purpose. */
  txn_id: number | null
  /** edit | archive | unarchive | delete | merge | categorize | recategorize | create | undo */
  action: string
  /** For edits: which field moved. Null for whole-row actions. */
  field: string | null
  old_value: string | null
  new_value: string | null
  /** What KIND of thing acted: user | ai | sync | dedup. */
  actor: string
  user_id: number | null
  /** Null for a scheduled sync, and for anything written before Artha recorded authorship. */
  user_name: string | null
  created_at: string | null
}

export interface Corrections {
  /** How often each field gets corrected by hand — what the categoriser should be learning from. */
  edits_by_field: Record<string, number>
  top_recategorizations: { to: string; count: number }[]
}

/** Without `txnId`, the household's recent changes across every transaction. */
export async function fetchAuditHistory(txnId?: number, limit = 100): Promise<AuditEvent[]> {
  const { data } = await api.get<AuditEvent[]>('/audit/history', {
    params: { ...(txnId != null ? { txn_id: txnId } : {}), limit },
  })
  return data
}

export async function fetchCorrections(): Promise<Corrections> {
  const { data } = await api.get<Corrections>('/audit/corrections')
  return data
}

/**
 * Put one change back.
 *
 * The server refuses what it cannot honestly reverse: a hard delete (the row is gone) and any
 * action it has no rule for. Both come back as 400 with a sentence, so show the message rather
 * than a generic failure.
 */
export async function undoAuditEvent(eventId: number): Promise<{ ok: boolean; restored: number; field: string | null }> {
  const { data } = await api.post(`/audit/undo/${eventId}`, {})
  return data
}
