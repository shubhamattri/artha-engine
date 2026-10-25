import { api } from './client'

/**
 * Taking money out of the wallet for something that is not a loan payment or an SIP.
 *
 * Those two paths already debit the wallet as a side effect of recording the payment — that is what
 * `funded_from: "wallet"` means, and it stays the right way to do it, because the payment and the
 * withdrawal are one act and should not be two entries. This is for everything else: the saved
 * money went on a thing, and the ledger should say so.
 *
 * The server refuses to overdraw. It comes back as a 400 whose message names both figures, so show
 * the message rather than a generic failure.
 */
export interface WalletSpend {
  amount: number
  /** The day it went out, "YYYY-MM-DD". */
  on?: string | null
  /** What it paid for. The wallet ledger is unreadable without it. */
  note?: string | null
  loan_id?: number | null
  transaction_id?: number | null
}

export async function spendFromWallet(body: WalletSpend): Promise<{ id: number; balance: number }> {
  const { data } = await api.post('/wallet/spend', body)
  return data
}
