import axios from 'axios'

// Dev: Vite (:5173) talks to the API on :8000.
// Prod: FastAPI serves the built UI, so the API is same-origin (/api) — works behind
// a private HTTPS host. Override with VITE_API_BASE if ever needed.
const API_BASE =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? 'http://localhost:8000/api' : '/api')

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true, // send/receive the httpOnly session cookie
})

// On any 401 from a data call, bounce to login (unless we're already on an auth page).
api.interceptors.response.use(
  (r) => r,
  (error) => {
    const status = error?.response?.status
    const path = window.location.pathname
    const onAuthPage = ['/login', '/forgot', '/reset'].some((p) => path.startsWith(p))
    if (status === 401 && !onAuthPage) {
      window.location.assign('/login')
    }
    return Promise.reject(error)
  },
)

// ---------- shared helpers ----------
export function currentMonth(): string {
  const d = new Date()
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
}

// ---------- money formatting ----------
// The household's currency is fetched ONCE (useDisplayCurrency, mounted in Layout) and parked here,
// so the ~40 formatINR call sites stay plain function calls instead of each growing a hook.
let _currency = 'INR'
let _locale: string | undefined = 'en-IN'

/** Point the money formatters at a currency. Called by useDisplayCurrency; safe to call repeatedly. */
export function setDisplayCurrency(currency?: string | null, locale?: string | null): void {
  const next = (currency || 'INR').toUpperCase()
  if (next !== _currency) _symbols.clear()   // the cached glyphs were formatted for the old locale
  _currency = next
  // Indian digit grouping (1,50,000) is right for INR and wrong for everything else, so a non-INR
  // household gets the runtime's own grouping for its currency instead.
  _locale = _currency === 'INR' ? locale || 'en-IN' : locale || undefined
}

export function displayCurrency(): string {
  return _currency
}

// Memoised: field labels call this on every render, and building an Intl.NumberFormat is not free.
const _symbols = new Map<string, string>()

/** The bare glyph for a currency, for labels and compact strings Intl's `style:'currency'` can't build. */
export function currencySymbol(code?: string): string {
  const c = (code || _currency).toUpperCase()
  const hit = _symbols.get(c)
  if (hit !== undefined) return hit
  let out: string
  try {
    const parts = new Intl.NumberFormat(c === _currency ? _locale : undefined, {
      style: 'currency', currency: c, maximumFractionDigits: 0,
    }).formatToParts(0)
    out = parts.find((p) => p.type === 'currency')?.value ?? c + ' '
  } catch {
    out = c + ' '
  }
  _symbols.set(c, out)
  return out
}

/** Full money, in the household's currency. `currency` overrides for a one-off (e.g. a foreign asset). */
export function formatMoney(n: number, currency?: string): string {
  const code = (currency || _currency).toUpperCase()
  try {
    return new Intl.NumberFormat(code === _currency ? _locale : undefined, {
      style: 'currency',
      currency: code,
      maximumFractionDigits: 0,
    }).format(n ?? 0)
  } catch {
    // An unknown/invalid ISO code must not blank a screen — fall back to the code plus the number.
    return `${code} ${Math.round(n ?? 0).toLocaleString()}`
  }
}

// Kept at their original 1-arg shape: both are passed straight to Recharts formatters, which would
// otherwise bind their own 2nd argument (the tick index) to `currency`.
/** Existing call sites — same signature, now follows the household's currency. */
export const formatINR = (n: number): string => formatMoney(n)

// Compact money — ONE source of truth (was duplicated 5 ways). Up to 3 decimals, trailing zeros
// trimmed: ₹1.02L (not 1.0L or 1.020L), ₹1.2L, ₹1L, ₹1.25 Cr, ₹12.5k. Lakh/crore is an INR
// convention, so other currencies get K/M/B rather than a wrong-but-familiar-looking "L".
export function shortMoney(n: number, currency?: string): string {
  const code = (currency || _currency).toUpperCase()
  const a = Math.abs(n ?? 0), s = (n ?? 0) < 0 ? '−' : ''
  const t = (x: number) => x.toFixed(2).replace(/\.?0+$/, '')
  const sym = currencySymbol(code)
  if (code === 'INR') {
    if (a >= 1e7) return `${s}${sym}${t(a / 1e7)} Cr`
    if (a >= 1e5) return `${s}${sym}${t(a / 1e5)}L`
    if (a >= 1e3) return `${s}${sym}${t(a / 1e3)}k`
    return `${s}${sym}${Math.round(a)}`
  }
  if (a >= 1e9) return `${s}${sym}${t(a / 1e9)}B`
  if (a >= 1e6) return `${s}${sym}${t(a / 1e6)}M`
  if (a >= 1e3) return `${s}${sym}${t(a / 1e3)}K`
  return `${s}${sym}${Math.round(a)}`
}

export const shortINR = (n: number): string => shortMoney(n)

// ---------- types ----------
// Every analytics response states the period it covers, so a card labels itself from the data
// instead of assuming a month the endpoint never applied.
export interface AnalyticsPeriod {
  mode: 'calendar_month' | 'instrument_cycle' | 'all_time'
  label: string
  from: string | null
  to: string | null
  start?: string | null
  end?: string | null
}

export interface MonthTrend {
  month: string
  total_spend: number
  total_income: number
  total_credit: number   // gross inflow incl. transfers + card-bill credits — not income
  net: number
}

export interface TrendResponse {
  period: AnalyticsPeriod
  months: MonthTrend[]
}

export interface LoanPayment {
  id: number
  date: string
  amount: number
  note?: string | null
  /** 'extra' is money on top of the EMI. 'skipped_emi' is the EMI that never left the account. */
  kind?: 'extra' | 'skipped_emi'
}
export interface Loan {
  id: number
  name: string
  lender: string
  /** What was sanctioned. On a flexi loan this is the LIMIT, not what was taken — use
   *  total_disbursed for the amount actually drawn. */
  original_principal: number
  /** Flexi only: the sum actually drawn across tranches. Null on standard and gold loans. */
  total_disbursed?: number | null
  /** When the loan was actually taken. Returned by the API but was missing from this type, so the
   *  loan cards could not show it. */
  disbursement_date?: string
  months_passed?: number
  /** Explicit override from the sanction letter. Null means the rule is derived from the rate type. */
  prepayment?: 'part' | 'foreclose_only' | 'none' | null
  prepayment_charge_pct?: number | null
  /** Whose principal this is. 'external' keeps the EMI in cashflow but takes the loan out of the
   *  payoff attack order, because prepaying it moves your money to someone else. */
  beneficiary?: 'household' | 'member' | 'external' 
  /** What actually governs: the override if set, otherwise the derived default. */
  prepayment_rule?: 'part' | 'foreclose_only' | 'none'
  prepayment_why?: string
  balance_inr: number
  rate_pct: number
  rate_type?: string
  emi_inr: number
  months_remaining: number
  closes_on?: string | null
  closes_in_months?: number
  holder: string
  category: string
  closed?: boolean
  closed_on?: string | null
  foir_exempt?: boolean
  loan_type?: string
  serviced_by_household?: boolean
  expected_monthly_payment?: number | null
  // gold-loan account fields (loan_type='gold')
  principal_outstanding?: number | null
  interest_due?: number | null
  payments?: LoanPayment[] | null
}

export interface DebtStrategyOrder { id: number; name: string; closes_in_months: number | null }
export interface DebtStep {
  id: number
  name: string
  closes_in_months: number | null   // null = never clears inside the simulation horizon
  monthly_payment: number           // what the simulation ACTUALLY paid in this loan's biggest month
  own_emi: number
  from_pool: number
  /** The month this step's headline figure becomes reachable, and the loans whose closure funds it.
   *  Shown so a later, larger figure does not read as money the household does not have. */
  peak_from_month?: number | null
  funded_by_closing?: string[]                 // spare cash + EMIs freed by loans already paid off
  extra: number
}
export interface DebtStrategy {
  key: string
  label: string
  description: string
  months: number
  complete: boolean
  baseline_complete: boolean
  total_interest: number
  /** Foreclosure charges. Kept apart from interest: this is the cost of CHOOSING a plan, not the
   *  cost of the borrowing. */
  prepayment_fees?: number
  total_cost?: number
  /** The loan spare cash reaches FIRST. Not the same as steps[0], which is sorted by what closes
   *  first — a large loan can take the money first and still finish last. */
  attack_next?: string | null
  /** EMIs + surplus. No step may ever ask for more than this in a month. */
  monthly_capacity?: number
  exceeds_capacity?: string[]
  // null when the "pay only your EMIs" baseline never finishes — there's no honest figure to show
  interest_saved: number | null
  months_saved: number | null
  never_amortises: string[]         // loans whose own payment only covers their interest
  order: DebtStrategyOrder[]
  steps: DebtStep[]
  current?: { extra: number; months: number; complete: boolean; total_interest: number }
}
export interface DebtSurplusContext {
  free_after_fixed: number
  variable_planned: number
  surplus: number
  over_budget: number
  actionable: number
  data_ok: boolean
  overspend: number
}
export type DebtRecoveryAction = { type: 'skip_sip'; sips: { id: number; name: string; amount: number }[] } | { type: 'withdraw'; id: number }
export interface DebtRecoveryOption { kind: 'pause_sip' | 'sinking' | 'borrow'; amount: number; available: number; title: string; sub: string; enough: boolean; action?: DebtRecoveryAction }
export interface DebtRecovery { gap: number; options: DebtRecoveryOption[]; non_loan_total: number; covered: boolean; shortfall: number; prevent: { total: number; sub: string } | null }
export interface DebtMove {
  kind: 'cover_overspend' | 'attack_debt' | 'hold' | 'on_track' | 'unknown'
  headline: string
  facts: Record<string, number | string | boolean>
  recovery: DebtRecovery | null
  target: DebtStep | null
}
export interface DebtStrategies {
  extra_per_month: number
  /** Whether the extra the dates assume has actually been going in. Null if it could not be measured. */
  adherence?: {
    months: { month: string; paid: number }[]
    planned_per_month: number
    actual_avg: number | null
    adherence_pct: number | null
    status: 'on_plan' | 'behind' | 'off_plan' | 'unknown'
    why: string
  } | null
  /** The same plans re-run at the OBSERVED rate. Present only when the household is not on plan. */
  at_actual?: Record<string, { extra: number; months: number; complete: boolean
                               total_interest: number; steps: DebtStep[] }>
  plan_extra?: number
  baseline: { months: number; total_interest: number } | null
  // The engine's pick — cheapest total interest among plans that CLEAR every loan, ties broken on the
  // earlier debt-free date. null when nothing clears: there is then no plan honest enough to recommend.
  recommended?: string | null
  recommended_why?: string | null
  options: DebtStrategy[]
  surplus_context?: DebtSurplusContext
  recovery?: DebtRecovery
  move?: DebtMove
}
export async function fetchDebtMoveNarration(): Promise<{ headline: string | null; ai: boolean }> {
  const { data } = await api.get<{ headline: string | null; ai: boolean }>('/debt/move-narration', { timeout: 20_000 })
  return data
}

export interface DebtResponse {
  loans: Loan[]
  total_balance: number
  total_emi: number
  monthly_interest: number
  strategies?: DebtStrategies
}

export interface Transaction {
  id: number
  date: string
  description: string
  merchant: string | null
  amount: number
  direction: 'debit' | 'credit'
  category: string | null
  confidence: string | null
  txn_type: string | null
  bank: string
  card_type: string
  card_last4: string
  stmt_month: string
}

export interface TransactionsResponse {
  transactions: Transaction[]
  total: number
}

export interface BudgetItem {
  category: string
  limit_inr: number
  spent_inr: number
  remaining_inr: number
  projected_inr?: number
  // The projection's honest span: low = nothing more is spent this cycle; high = the old flat-rate
  // assumption. `one_off_inr` is the part of spend that is a lump, counted once and never scaled.
  projected_low_inr?: number
  projected_high_inr?: number
  one_off_inr?: number
  recurring_inr?: number
  surprise_allowance_inr?: number
  projection_confidence?: 'low' | 'medium' | 'high'
  status?: 'ok' | 'will_breach' | 'over'
  ids?: number[]   // exact txn ids behind this row → drill reconciles to the rupee in any lens
}

export interface BudgetWeek {
  /** `end` is the bucket's LAST day, inclusive. Use `span` for the day count rather than
   *  subtracting the dates — that convention is exactly what produced "runs out 29 Aug, 15 days
   *  before the cycle ends" when it was re-derived by hand. */
  week: number; start: string; end: string; span: number; spent: number; target: number
  is_current: boolean; is_future: boolean; on_track: boolean | null
}
export interface BudgetResponse {
  month: string
  mode?: string
  is_current?: boolean   // is this the LIVE period (drives days-left vs closed + the next-step button)
  items: BudgetItem[]
  refunds_inr?: number
  net_spent_inr?: number
  cycle?: { days_total: number; days_elapsed: number; days_left: number }
  period_start?: string   // the true window this period covers (labels a cycle by its dates)
  period_end?: string
  weeks?: BudgetWeek[]
  weekly_target?: number
  total_target_inr?: number
  overshoot_inr?: number
  /**
   * The three facts the cumulative chart makes, computed server-side because api/budget owns spend
   * and its projection. `runs_out_at` is the exact position in the period (0-1) and exists separately
   * from `runs_out_on` because the marker needs precision and a human needs a date — deriving the
   * marker's x from the rounded date left the dot floating below the ceiling it is touching.
   */
  projection?: {
    projected_inr: number
    over_inr: number
    on_pace_inr: number
    ahead_by_inr: number
    runs_out_on: string | null
    runs_out_at: number | null
    days_before_end: number | null
  }
}

// Category × week grid (Plan). Each cell carries the txn ids behind it so a tap drills to exact rows.
export interface CategoryWeekRow { category: string; weeks: number[]; txn_ids: number[][]; total: number }
export interface CategoryWeeksResponse {
  month: string; mode: string
  weeks: { week: number; start: string; end: string }[]
  categories: CategoryWeekRow[]
}
export async function fetchCategoryWeeks(anchor: string, mode: string): Promise<CategoryWeeksResponse> {
  const { data } = await api.get<CategoryWeeksResponse>('/budget/category-weeks', { params: { anchor, mode } })
  return data
}

// ---------- analytics (history: month-on-month category matrix + budget-learning recommendations) ----
export interface MatrixRow {
  category: string; byMonth: Record<string, number>; total: number; avg: number
  idsByMonth?: Record<string, number[]>  // exact txn ids behind each cell → drill reconciles to the rupee
  yoyByMonth?: Record<string, number>    // % vs same month prior year — present only where prior-year data exists
}
export interface CategoryMatrix {
  period?: AnalyticsPeriod
  months: string[]; categories: MatrixRow[]; grand_total_by_month: Record<string, number>
  refunds_by_month?: Record<string, number>
}
export async function fetchCategoryMatrix(from_month?: string, to_month?: string): Promise<CategoryMatrix> {
  const { data } = await api.get<CategoryMatrix>('/analytics/category-matrix', {
    params: { ...(from_month ? { from_month } : {}), ...(to_month ? { to_month } : {}) },
  })
  return data
}

// Cycle lens of the same matrix — columns are household billing cycles instead of calendar months.
export interface CycleCol { key: string; start: string; end: string }
export interface CycleMatrixRow {
  category: string; byCycle: Record<string, number>; idsByCycle: Record<string, number[]>
  total: number; avg: number
}
export interface CycleMatrix {
  period?: AnalyticsPeriod
  cycles: CycleCol[]; categories: CycleMatrixRow[]; grand_total_by_cycle: Record<string, number>
  refunds_by_cycle?: Record<string, number>
}
export async function fetchCycleMatrix(count = 12): Promise<CycleMatrix> {
  const { data } = await api.get<CycleMatrix>('/analytics/cycle-matrix', { params: { count } })
  return data
}

// The year. `net_spent_inr` is null for a period that has not started — null means "has not
// happened", 0 means "happened and cost nothing", and the screen must not collapse the two.
export interface YearMonth {
  month: string; target_inr: number
  net_spent_inr: number | null; cumulative_inr: number | null; refunds_inr?: number
  /** min(spent, target) and max(0, spent - target), per month. Always sum to net_spent_inr. */
  within_target_inr?: number; above_target_inr?: number
  window: { start: string; end: string }
  state: 'closed' | 'partial' | 'future'
}
export interface YearPurchase {
  id: number; month: string; date: string; category: string
  description: string | null; merchant: string | null; amount_inr: number
}
export interface YearResponse {
  year: number
  /** '2026' on a calendar year, '2026–27' when it straddles two. */
  label: string
  /** 1–12. April here, January elsewhere — the household's, never the code's. */
  start_month: number
  mode: string
  envelope_inr: number; net_spent_inr: number
  within_target_inr: number; above_target_inr: number
  /** Periods that happened with NO target set. Their spend is all in above_target. */
  unbudgeted_periods: number
  purchase_count: number
  months: YearMonth[]
  top_purchases: YearPurchase[]
  exhausted: YearPurchase | null
  outlook: {
    months_closed: number; months_remaining: number; rate_inr: number; year_end_inr: number
    needed_per_month_inr: number | null; breach: boolean
  }
}
/**
 * The readable name inside a bank's description string. DISPLAY ONLY — the stored row is never
 * touched, and the raw text stays searchable on Spending.
 *
 * A UPI line arrives as `UPI/EXAMPLE MOTORS/vyapar.222233334444@hdfcbank/5550001111@ptyes`, and a
 * card line often carries an 11-digit reference before the merchant. Shown raw, a list of large
 * purchases becomes a wall of payment plumbing and the one thing the reader is there to do —
 * recognise what they bought — gets harder the longer the string is.
 */
export function merchantLabel(text?: string | null): string {
  const raw = (text || '').trim()
  if (!raw) return ''
  const upi = raw.match(/^UPI[/-](.+)$/i)
  const body = upi ? (upi[1].split('/')[0] || upi[1]) : raw
  return body.replace(/^\d{8,}\s+/, '').replace(/\s+/g, ' ').trim() || raw
}

export async function fetchYear(year?: number, mode = 'cycle'): Promise<YearResponse> {
  const { data } = await api.get<YearResponse>('/annual', {
    params: { ...(year ? { year } : {}), mode },
  })
  return data
}

export interface Recommendation {
  category: string; current_target: number; suggested: number; band: [number, number]
  basis: string; months_used: number; signal: 'unrealistic' | 'rising' | 'falling' | 'volatile' | 'steady'
  delta_pct: number; why: string; ai: boolean
}
export interface RecommendationsResponse {
  period?: AnalyticsPeriod
  generated_for: string; window: number; unit?: string; cycle_mode?: string
  recommendations: Recommendation[]
  new_categories: { category: string; months_seen: number; suggested: number }[]
  move_to_fixed: { category: string; avg: number; cov: number }[]
}
export async function fetchRecommendations(cycle_mode = 'instrument_cycle'): Promise<RecommendationsResponse> {
  // LLM narration is bounded ~14s server-side; 25s ceiling means genuinely stuck → error+retry.
  const { data } = await api.get<RecommendationsResponse>('/analytics/recommendations', {
    params: { cycle_mode }, timeout: 25_000,
  })
  return data
}

export interface HealthBucket { label: string; amount: number; pct: number; color: string }
export interface Health {
  income: number; buckets: HealthBucket[]
  savings_rate: number; emi_rate: number; fixed_rate: number; insurance_rate: number; free_rate: number
}
export async function fetchHealth(): Promise<Health> {
  const { data } = await api.get<Health>('/health')
  return data
}

export interface Insight { title: string; body: string; action?: string; tone: string; category?: string | null; ai?: boolean }
export async function fetchInsights(mode: string = 'cycle'): Promise<{ insights: Insight[]; source: string }> {
  // 25s ceiling: the backend LLM narrator is bounded to ~18s, so if this fires the request is
  // genuinely stuck — axios rejects, the cockpit shows an error+retry instead of hanging forever.
  const { data } = await api.get<{ insights: Insight[]; source: string }>('/insights', { params: { mode }, timeout: 25_000 })
  return data
}

export interface ChatMsg { role: 'user' | 'assistant'; content: string }
export async function askAdvisor(messages: ChatMsg[], period: 'cycle' | 'month' = 'cycle'): Promise<{ reply: string }> {
  // Ask Artha runs on Opus (reasoning tier) — a full planning answer can take 30–60s. Give it room.
  // `period` = the lens the user is viewing, so 'this period' answers match the rest of the app.
  const { data } = await api.post<{ reply: string }>('/advisor', { messages, period }, { timeout: 90_000 })
  return data
}

// ---------- fetch helpers ----------
export async function fetchTrend(): Promise<TrendResponse> {
  const { data } = await api.get<TrendResponse>('/analytics/trend')
  return data
}

export async function fetchDebt(): Promise<DebtResponse> {
  const { data } = await api.get<DebtResponse>('/debt')
  return data
}

// ---------- commitments (planned recurring outflows vs actual debits) ----------
export interface CommitmentPaid {
  ref: string; kind: string; name: string; amount: number; category: string | null
  actual: number; txn_id: number; txn_date: string; payee: string; via?: string; signature?: string
}
export interface CommitmentMissing {
  ref: string; kind: string; name: string; amount: number; category: string | null
}
export interface CommitmentExtra {
  txn_id: number; amount: number; category: string; payee: string; date: string
}
export interface ReconcileResponse {
  month: string
  paid: CommitmentPaid[]; missing: CommitmentMissing[]; extra: CommitmentExtra[]
  expected_total: number; paid_total: number; missing_total: number; extra_total: number
}
export async function fetchCommitmentsReconcile(month?: string): Promise<ReconcileResponse> {
  const { data } = await api.get<ReconcileResponse>('/commitments/reconcile', {
    params: month ? { month } : {},
  })
  return data
}

// Exception queue — link a 'missing' commitment to the debit that actually paid it (person transfers)
export interface CommitmentCandidate {
  txn_id: number; amount: number; payee: string; date: string; category: string
  signature: string; linked_here: boolean
}
export interface CandidatesResponse {
  ref: string; name?: string; amount?: number; candidates: CommitmentCandidate[]
}
export async function fetchCommitmentCandidates(ref: string, month?: string): Promise<CandidatesResponse> {
  const { data } = await api.get<CandidatesResponse>('/commitments/candidates', {
    params: { ref, ...(month ? { month } : {}) },
  })
  return data
}
export async function linkCommitment(txn_id: number, commitment_ref: string): Promise<{ signature: string }> {
  const { data } = await api.post('/commitments/link', { txn_id, commitment_ref })
  return data
}
export async function unlinkCommitment(signature: string): Promise<{ ok: boolean }> {
  const { data } = await api.post('/commitments/unlink', { signature })
  return data
}

// ---------- category consistency review (merchants split across categories) ----------
export interface ConsistencyMerchant {
  key: string; display: string; total: number
  categories: { category: string; count: number; amount: number }[]
}
export async function fetchCategoryConsistency(): Promise<{ merchants: ConsistencyMerchant[] }> {
  const { data } = await api.get<{ merchants: ConsistencyMerchant[] }>('/transactions/category-consistency')
  return data
}
export interface DupReviewRow {
  id: number; fingerprint: string; date: string; merchant: string | null; description: string
  amount: number; source: string; card_last4: string; category: string | null
  occurred_at: string | null; bank_ref: string | null
}
export interface DupCandidate { primary: DupReviewRow; duplicate: DupReviewRow; amount: number; why: string }
export async function fetchDuplicateReview(): Promise<{ candidates: DupCandidate[] }> {
  const { data } = await api.get<{ candidates: DupCandidate[] }>('/transactions/duplicate-review')
  return data
}
export async function mergeDuplicate(primary_id: number, duplicate_id: number): Promise<{ ok: boolean }> {
  const { data } = await api.post('/transactions/duplicate-review/merge', { primary_id, duplicate_id })
  return data
}
export async function dismissDuplicate(fp_a: string, fp_b: string): Promise<{ ok: boolean }> {
  const { data } = await api.post('/transactions/duplicate-review/dismiss', { fp_a, fp_b })
  return data
}

export async function setMerchantCategory(key: string, category: string): Promise<{ changed: number }> {
  const { data } = await api.post('/transactions/merchant-category', { key, category })
  return data
}

// ---------- ledger housekeeping (whole-ledger sweeps; all four take dry_run as a QUERY param, no body) ----------
export interface NormalizeMerchantsResult {
  changed: number
  /** Rows a canonical brand would have moved, left alone because the household taught that merchant. */
  skipped_user: number
  by_merchant: Record<string, { to: string; from: Record<string, number>; n: number }>
  dry_run: boolean
}
export async function normalizeMerchants(dryRun: boolean): Promise<NormalizeMerchantsResult> {
  const { data } = await api.post<NormalizeMerchantsResult>(
    '/transactions/normalize-merchants', null, { params: { dry_run: dryRun } })
  return data
}

export interface RecategorizeResult {
  total: number
  reclassified: number
  /** TOP 20 ONLY — the server truncates. `reclassified` is the real count; never total these. */
  moves: Record<string, number>
  dry_run: boolean
}
export async function recategorizeAll(dryRun: boolean): Promise<RecategorizeResult> {
  const { data } = await api.post<RecategorizeResult>(
    '/transactions/recategorize', null, { params: { dry_run: dryRun } })
  return data
}

export interface NormalizeBanksResult {
  changed: number
  moves: Record<string, number>
  dry_run: boolean
}
export async function normalizeBanks(dryRun: boolean): Promise<NormalizeBanksResult> {
  const { data } = await api.post<NormalizeBanksResult>(
    '/transactions/normalize-banks', null, { params: { dry_run: dryRun } })
  return data
}

/** One endpoint, four shapes. No dry run — it writes or it does nothing.
 *  The success shape counts rows in `rows_updated`; every no-op shape counts them in `updated`. */
export interface CategorizeAiResult {
  ok: boolean
  reason?: string       // ok:false — why nothing happened
  note?: string         // ok:true, distinct 0 — nothing left to do
  distinct?: number
  categorized?: number
  rows_updated?: number
  updated?: number
}
export interface PairTransfersResult {
  pairs: number
  transactions_tagged: number
  /** What those transfers were inflating spend by before they were tagged. */
  eliminated_total: number
}

/** No dry run: pairing is what the read costs, and the tag it writes is reversible by re-running. */
export async function pairInternalTransfers(): Promise<PairTransfersResult> {
  const { data } = await api.post<PairTransfersResult>('/transactions/reconcile', null)
  return data
}

export async function categorizeAi(limit = 400): Promise<CategorizeAiResult> {
  const { data } = await api.post<CategorizeAiResult>(
    '/transactions/categorize-ai', null, { params: { limit } })
  return data
}

export async function fetchTransactions(params?: {
  month?: string
  category?: string
  bank?: string
  txn_type?: string
  card_last4?: string
  cycle?: string
  ids?: string        // exact set (from a category×week cell) — supersedes month/cycle
  start?: string      // inclusive date window (YYYY-MM-DD) — general historical-range primitive
  end?: string
  limit?: number
  offset?: number
}): Promise<TransactionsResponse> {
  const { data } = await api.get<TransactionsResponse>('/transactions', { params })
  return data
}

export interface TxnFacets {
  months: string[]
  types: string[]
  cards: { bank: string; card_type: string; card_last4: string; label: string }[]
}
export async function fetchTransactionFacets(): Promise<TxnFacets> {
  const { data } = await api.get<TxnFacets>('/transactions/facets')
  return data
}

export interface CategoryOption {
  value: string
  is_spend: boolean
  custom?: boolean   // true = a household-defined budget category (renamable / deletable)
}
export async function fetchCategories(): Promise<CategoryOption[]> {
  const { data } = await api.get<{ categories: CategoryOption[] }>('/transactions/categories')
  return data.categories
}

// ── Household-defined budget categories (added from the Plan) ─────────────────────────
export interface CustomCategory {
  id: number
  name: string
  sort_order: number
}
export interface CategoryRuleRow {
  id: number
  signature: string
  merchant: string | null
  /** user = you confirmed it · ai = the model guessed · rule = built in. */
  kind: string
  hits: number
}
export async function createCategory(name: string): Promise<CustomCategory> {
  const { data } = await api.post<CustomCategory>('/categories', { name })
  return data
}
export async function renameCategory(id: number, name: string): Promise<CustomCategory> {
  const { data } = await api.patch<CustomCategory>(`/categories/${id}`, { name })
  return data
}
/** `into` chooses where the history lands. Omitted, it goes to Personal / Misc as it always did. */
export async function deleteCategory(id: number, into?: string): Promise<{ reassigned_to: string }> {
  const { data } = await api.delete<{ reassigned_to: string }>(`/categories/${id}`,
    { params: into ? { into } : undefined })
  return data
}
/** Every category this household can manage, built-in and its own, in one list. */
export interface ManagedCategory {
  /** The canonical name, as stored on transactions. The join key: it never moves for a built-in. */
  category: string
  /** Only a household's own category has a row id. A built-in has no row and cannot be deleted. */
  id: number | null
  /** What to show. Falls back to the canonical name when no label is set. */
  display_name: string
  builtin: boolean
  color: string | null
  active: boolean
  is_variable: boolean
  /** Seeds months that have no budget row of their own. */
  default_target_inr: number | null
  /** THIS month's actual budget row, the figure the Plan shows. null = no row, which is not zero. */
  current_target_inr: number | null
  sort_order: number | null
}
export async function fetchAllCategories(): Promise<ManagedCategory[]> {
  const { data } = await api.get<{ categories: ManagedCategory[] }>('/categories/all')
  return data.categories
}
/**
 * Set colour / target / archive / committed / label on ANY category, keyed by canonical name.
 *
 * `display_name` is a LABEL. For a built-in the stored name is a key that forty places compare
 * against, so it never moves; you see your word for it. A custom category's real rename is
 * renameCategory, which rewrites the household's data because there the name IS the identity.
 */
export async function updateCategorySettings(name: string, patch: Partial<{
  display_name: string | null; active: boolean; is_variable: boolean | null
  color: string | null; default_target_inr: number | null
}>): Promise<ManagedCategory> {
  const { data } = await api.patch<ManagedCategory>(
    `/categories/by-name/${encodeURIComponent(name)}/properties`, patch)
  return data
}
export async function orderCategories(names: string[]): Promise<void> {
  await api.post('/categories/order', { names })
}
/** Fold this category into another: transactions, targets and learned rules all move, then it goes. */
export async function mergeCategory(id: number, into: string): Promise<{ merged_into: string }> {
  const { data } = await api.post<{ merged_into: string }>(`/categories/${id}/merge`, { into })
  return data
}

/** Which merchants land here, and whether that came from you or from the model. */
export async function fetchCategoryRules(id: number): Promise<CategoryRuleRow[]> {
  const { data } = await api.get<{ rules: CategoryRuleRow[] }>(`/categories/${id}/rules`)
  return data.rules
}
/** Pull selected merchants into a new category, taking their past transactions with them. */
export async function splitCategory(id: number, name: string, signatures: string[]) {
  const { data } = await api.post<{ rules_moved: number; transactions_moved: number }>(
    `/categories/${id}/split`, { name, signatures })
  return data
}

export type TransactionPatch = Partial<{
  amount: number
  date: string
  merchant: string
  description: string
  direction: 'debit' | 'credit'
  account: string
  category: string
  txn_type: string
}>
export async function updateTransaction(id: number, patch: TransactionPatch) {
  const { data } = await api.patch(`/transactions/${id}`, patch)
  return data
}
export async function deleteTransaction(id: number, hard = false): Promise<void> {
  await api.delete(`/transactions/${id}`, { params: hard ? { hard: true } : undefined })
}

// ---------- reconciliation / duplicates ----------
export interface DupRow {
  id: number; date: string; description: string; merchant: string | null
  amount: number; direction: string; source: string; category: string | null; card_last4: string
}
export interface DupGroup {
  primary: DupRow; duplicates: DupRow[]; confidence: 'high' | 'low'; wasted: number
}
export interface DupPreview {
  groups: DupGroup[]; group_count: number; high_confidence: number
  duplicate_rows: number; auto_mergeable_rows: number
}
export async function previewDuplicates(): Promise<DupPreview> {
  const { data } = await api.get<DupPreview>('/reconcile/duplicates')
  return data
}
export async function applyDuplicates(all = false): Promise<{ ok: boolean; merged_rows: number }> {
  const { data } = await api.post('/reconcile/duplicates', { all })
  return data
}
export async function undoDuplicates(primary_id?: number): Promise<{ ok: boolean; restored: number }> {
  const { data } = await api.post('/reconcile/duplicates/undo', primary_id ? { primary_id } : {})
  return data
}

// `anchor` is an ISO date; the backend returns the cycle/month CONTAINING it (unambiguous stepping).
export async function fetchBudget(anchor: string, mode: string = 'cycle'): Promise<BudgetResponse> {
  const { data } = await api.get<BudgetResponse>('/budget', { params: { anchor, mode } })
  return data
}
export async function saveBudget(month: string, items: { category: string; monthly_limit_inr: number }[]) {
  const { data } = await api.post('/budget', { month, items })
  return data
}

export async function uploadStatement(fd: FormData): Promise<unknown> {
  const { data } = await api.post('/upload', fd)
  return data
}

// ---------- Gmail auto-pull ----------
export async function gmailStatus(): Promise<{ connected: boolean; query: string }> {
  const { data } = await api.get('/gmail/status')
  return data
}
export async function gmailSync(): Promise<{ fetched: number; ingested_ok: number; transactions_new: number; results: any[] }> {
  const { data } = await api.post('/gmail/sync')
  return data
}

/** Pull per-transaction bank ALERT emails (daily spend capture, no upload). */
export async function gmailSyncAlerts(days = 21): Promise<{ ok: boolean; scanned?: number; added?: number; note?: string }> {
  const { data } = await api.post(`/transactions/sync-gmail-alerts?days=${days}`)
  return data
}

/** Capture bank transaction SMS forwarded to the Mac's Messages (100% coverage, all-Apple household). */
export async function syncSms(days = 30): Promise<{ ok: boolean; added?: number; note?: string }> {
  const { data } = await api.post(`/transactions/sync-sms?days=${days}`)
  return data
}

/** ONE-button sync — SMS + Gmail + categorize + auto-merge. Returns {summary:{new,merged}, ...}. */
export async function syncAll(days = 4): Promise<{
  /** `failed` names the self-heal steps that did not run. A sync can finish eight of nine jobs, and
   *  reporting that as "Up to date" is how a stopped duplicate-merge goes unnoticed for weeks. */
  summary: { new: number; merged: number; failed: string[] }
  sms: { ok: boolean; added?: number; error?: string }
  gmail: { ok: boolean; added?: number; error?: string }
}> {
  const { data } = await api.post(`/transactions/sync?days=${days}`)
  return data
}

// ---------- loans (CRUD + detail + preclose) ----------
export interface LoanInput {
  name: string
  lender: string
  holder: string
  category: string
  original_principal: number
  annual_rate_pct: number
  tenure_months: number
  disbursement_date: string
  emi_override?: number | null
  notes?: string | null
  foir_exempt?: boolean
  serviced_by_household?: boolean
  expected_monthly_payment?: number | null
  rate_type?: string
  /** Null means derive from the rate type (RBI forbids a charge on floating loans to individuals);
   *  a value is an explicit override from the sanction letter. */
  prepayment?: 'part' | 'foreclose_only' | 'none' | null
  prepayment_charge_pct?: number | null
  /** Whose principal this is. 'external' keeps the EMI in cashflow but takes the loan out of the
   *  payoff attack order, because prepaying it moves your money to someone else. */
  beneficiary?: 'household' | 'member' | 'external' 
  loan_type?: string
  disbursed_amount?: number | null
  moratorium_months?: number
  moratorium_interest_mode?: string | null
  disbursements?: Disbursement[] | null
}

export interface Disbursement {
  amount: number
  date: string
  note?: string
}

export async function addDisbursement(
  id: number,
  body: { amount: number; date: string; note?: string },
): Promise<any> {
  const { data } = await api.post(`/debt/${id}/disburse`, body)
  return data
}
export async function deleteDisbursement(id: number, index: number): Promise<any> {
  const { data } = await api.delete(`/debt/${id}/disburse/${index}`)
  return data
}

export async function fetchLoanDetail(id: number): Promise<any> {
  const { data } = await api.get(`/debt/${id}`)
  return data
}
// gold-loan flexible payment ledger — any amount, any date, several or none
export async function skipLoanEmi(id: number, body: { date: string; amount?: number; note?: string }) {
  const { data } = await api.post(`/debt/${id}/skip`, body)
  return data
}

export interface Wallet {
  starts_from: string | null
  balance: number
  saved_total: number
  spent_total: number
  inflows: { cycle_end: string; saved: number }[]
  outflows: { id: number; amount: number; on: string; loan_id: number | null; note: string | null }[]
  note: string
}

export async function fetchWallet(): Promise<Wallet> {
  const { data } = await api.get('/wallet')
  return data
}

export async function addLoanPayment(
  /* funded_from defaults to bank server-side: a payment made without choosing must never quietly
     drain the wallet. */
  id: number, body: { date: string; amount: number; note?: string; funded_from?: string },
): Promise<Loan> {
  const { data } = await api.post(`/debt/${id}/payments`, body)
  return data
}
export async function deleteLoanPayment(id: number, paymentId: number): Promise<Loan> {
  const { data } = await api.delete(`/debt/${id}/payments/${paymentId}`)
  return data
}
export async function createLoan(body: LoanInput): Promise<Loan> {
  const { data } = await api.post('/debt', body)
  return data
}
export async function updateLoan(id: number, body: LoanInput): Promise<Loan> {
  const { data } = await api.put(`/debt/${id}`, body)
  return data
}
export async function precloseLoan(
  id: number,
  mode: 'foreclose' | 'prepay',
  amount?: number,
): Promise<Loan> {
  const { data } = await api.post(`/debt/${id}/preclose`, { mode, amount })
  return data
}
export async function deleteLoan(id: number): Promise<void> {
  await api.delete(`/debt/${id}`)
}

// ---------- cash flow (two-ledger engine) ----------
export interface CashflowLine {
  id: number
  kind: 'income' | 'fixed' | 'saving'
  label: string
  amount_inr: number
  holder: string | null
  active: boolean
  start_date?: string | null
  end_date?: string | null
}
export interface CashflowSummary {
  income: number
  emi: number
  fixed: number
  saving: number
  invested: number
  unallocated: number
  sinking: number
  insurance: number
  leftover: number
  lines: { income: CashflowLine[]; fixed: CashflowLine[]; saving: CashflowLine[] }
}
export async function fetchCashflowSummary(): Promise<CashflowSummary> {
  const { data } = await api.get<CashflowSummary>('/cashflow/summary')
  return data
}
export async function createCashflowLine(body: Omit<CashflowLine, 'id'>): Promise<CashflowLine> {
  const { data } = await api.post('/cashflow', body)
  return data
}
export async function updateCashflowLine(id: number, body: Omit<CashflowLine, 'id'>): Promise<CashflowLine> {
  const { data } = await api.put(`/cashflow/${id}`, body)
  return data
}
export async function deleteCashflowLine(id: number): Promise<void> {
  await api.delete(`/cashflow/${id}`)
}

// ---------- net worth (assets) ----------
export interface Asset {
  id: number
  name: string
  asset_type: string
  holder: string | null
  ticker: string | null
  quantity: number | null
  currency: string
  manual_value_inr: number | null
  notes: string | null
  meta: Record<string, any> | null
  value_inr: number
  priced: 'live' | 'cached' | 'manual' | 'computed'
  unit_price: number | null
  as_of: string | null
  /** present only on the synthetic "proceeds receivable" line a disposal adds — not a real asset row */
  disposal_id?: number
  note?: string
}
export interface NetworthResponse {
  assets: Asset[]
  total_inr: number
  by_type: Record<string, number>
}

export interface GoldHolding {
  name: string
  grams: number | null
  purity: string | null
  rate_per_g: number | null
  value: number
  as_of: string | null
}
export interface GoldCollateral {
  ltv: number
  real_value: number
  total_grams: number
  holdings: GoldHolding[]
  priced_at: string | null
  gold_loans: { name: string; balance: number }[]
  loan_pledged: number
  external_pledged: number
  pledged_value: number
  net_value: number
  borrow_room: number
}
export interface NetworthSummary {
  net_worth: number
  total_assets: number
  total_liabilities: number
  gold_collateral: GoldCollateral
}
export async function fetchNetworthSummary(): Promise<NetworthSummary> {
  const { data } = await api.get<NetworthSummary>('/networth/summary')
  return data
}
export type AssetInput = {
  name: string
  asset_type: string
  holder?: string | null
  ticker?: string | null
  quantity?: number | null
  currency?: string
  manual_value_inr?: number | null
  meta?: Record<string, any> | null
  notes?: string | null
}
export async function fetchNetworth(refresh = false): Promise<NetworthResponse> {
  const { data } = await api.get<NetworthResponse>('/networth', { params: { refresh } })
  return data
}
export async function createAsset(body: AssetInput): Promise<Asset> {
  const { data } = await api.post('/networth', body)
  return data
}
export async function updateAsset(id: number, body: AssetInput): Promise<Asset> {
  const { data } = await api.put(`/networth/${id}`, body)
  return data
}
export async function deleteAsset(id: number): Promise<void> {
  await api.delete(`/networth/${id}`)
}

// ---------- disposals (recording a sale) ----------
// costs_inr and loan_settled_inr stay separate on purpose, never one "deductions" number: costs
// reduce the taxable gain, a loan settled out of the proceeds reduces it by nothing.
export type DisposalInput = {
  disposed_on: string
  gross_proceeds_inr: number
  acquired_on?: string | null
  quantity?: number | null
  costs_inr?: number
  loan_settled_inr?: number
  receivable_inr?: number
  cost_basis_inr?: number | null
  notes?: string | null
}
/** One instalment of a receivable actually arriving — the audit trail behind a fallen receivable. */
export interface DisposalReceipt {
  id: number
  received_on: string
  amount_inr: number
  asset_id: number | null
  notes: string | null
}
export type ReceiptInput = {
  received_on: string
  amount_inr: number
  notes?: string | null
}
export interface Disposal {
  id: number | null
  asset_id?: number
  asset: string
  asset_class: string
  disposed_on: string
  acquired_on?: string | null
  quantity?: number | null
  gross_proceeds_inr: number
  costs_inr?: number
  loan_settled_inr: number
  receivable_inr: number
  net_received_inr: number
  cost_basis_inr?: number | null
  notes?: string | null
  /** null = the cost basis is unknown, so the gain is NOT computable. Never render this as zero. */
  gain_inr: number | null
  is_long_term: boolean | null
  months_held: number | null
  tax_inr: number | null
  method: string
  why: string
  /** what has landed against the receivable so far, from the receipt ledger */
  received_inr?: number
  receipts?: DisposalReceipt[]
}
export interface DisposalsResponse {
  disposals: Disposal[]
  estimated_tax_inr: number
  unpriced_count: number
}
export async function fetchDisposals(): Promise<DisposalsResponse> {
  const { data } = await api.get<DisposalsResponse>('/disposals'); return data
}
export async function disposeAsset(assetId: number, body: DisposalInput): Promise<Disposal> {
  const { data } = await api.post(`/assets/${assetId}/dispose`, body); return data
}
export async function updateDisposal(id: number, body: Partial<DisposalInput>): Promise<Disposal> {
  const { data } = await api.patch(`/disposals/${id}`, body); return data
}
export async function deleteDisposal(id: number): Promise<void> {
  await api.delete(`/disposals/${id}`)
}
// Money landing against a receivable. ONE call, because it moves BOTH sides: the receivable falls
// and the sale's cash row rises. Editing receivable_inr alone deletes that money from net worth.
export async function recordReceipt(disposalId: number, body: ReceiptInput): Promise<Disposal> {
  const { data } = await api.post(`/disposals/${disposalId}/receipts`, body); return data
}
export async function undoReceipt(disposalId: number, receiptId: number): Promise<Disposal> {
  const { data } = await api.delete(`/disposals/${disposalId}/receipts/${receiptId}`); return data
}

// ---------- recurring / accruing investments (SIP, RD, EPF, PPF) ----------
// What the money is FOR, independent of the instrument holding it. 'donation' is money already
// spoken for: a real bank outflow every month, but it never becomes household wealth.
export type RecurringPurpose = 'wealth' | 'donation'
export interface RecurringInvestment {
  id: number
  name: string
  kind: 'sip' | 'rd' | 'epf' | 'ppf'
  purpose: RecurringPurpose
  holder: string | null
  opening_balance: number
  as_of_date: string
  monthly_contribution: number
  employer_contribution: number
  annual_rate_pct: number
  compounding: string
  maturity_date: string | null
  // The ledger. A negative amount (or negative units) is a REDEMPTION — money coming back out.
  contributions: { amount?: number; date: string; note?: string; units?: number; nav?: number; funded_from?: string }[]
  active: boolean
  from_takehome: boolean
  category: string
  notes: string | null
  current_value: number
  total_contributed: number
  interest_earned: number
  months_elapsed: number
  monthly_inflow: number
  takehome_outflow: number
  maturity_value: number | null
  matured: boolean
  // market-linked (MF) only:
  scheme_code?: string
  units?: number
  nav?: number | null
  nav_date?: string | null
  priced?: 'live' | 'manual'
  xirr_pct?: number
  return_pct?: number
}
export interface RecurringResponse {
  investments: RecurringInvestment[]
  total_value: number
  total_contributed: number
  total_interest: number
  monthly_inflow: number
  takehome_outflow: number
  at_source_inflow: number
  wealth_outflow: number       // take-home outflow that actually builds the corpus
  donation_outflow: number     // committed to give away — leaves the bank, never becomes wealth
  total_wealth_value: number   // total_value less anything held for a donation
  by_kind: Record<string, number>
}

// ---------- sinking funds (monthly set-asides for lumpy expenses) ----------
export interface SinkingFund {
  id: number
  name: string
  monthly_contribution: number
  opening_balance: number
  as_of_date: string
  target_annual: number | null
  holder: string | null
  withdrawals: { amount: number; date: string; note?: string }[]
  active: boolean
  notes: string | null
  months_elapsed: number
  paid_in: number
  spent: number
  balance: number
}
export interface SinkingResponse {
  funds: SinkingFund[]
  total_balance: number
  monthly_set_aside: number
}
export type SinkingInput = {
  name: string
  monthly_contribution?: number
  opening_balance?: number
  as_of_date: string
  target_annual?: number | null
  holder?: string | null
  active?: boolean
  notes?: string | null
}
export async function fetchSinking(): Promise<SinkingResponse> {
  const { data } = await api.get<SinkingResponse>('/sinking')
  return data
}
export async function createSinking(body: SinkingInput): Promise<SinkingFund> {
  const { data } = await api.post('/sinking', body)
  return data
}
export async function updateSinking(id: number, body: SinkingInput): Promise<SinkingFund> {
  const { data } = await api.put(`/sinking/${id}`, body)
  return data
}
export async function pauseRecurring(id: number, active = false): Promise<RecurringInvestment> {
  const { data } = await api.post<RecurringInvestment>(`/recurring/${id}/pause`, null, { params: { active } })
  return data
}
export async function skipRecurring(id: number, amount: number): Promise<RecurringInvestment> {
  // skip PART of this month's SIP contribution (frees exactly `amount`, resumes next month)
  const { data } = await api.post<RecurringInvestment>(`/recurring/${id}/skip`, { amount })
  return data
}
export async function withdrawSinking(id: number, body: { amount: number; date: string; note?: string }): Promise<SinkingFund> {
  const { data } = await api.post(`/sinking/${id}/withdraw`, body)
  return data
}
export async function deleteSinking(id: number): Promise<void> {
  await api.delete(`/sinking/${id}`)
}

// ---------- insurance policies ----------
export interface InsurancePolicy {
  id: number; provider: string; plan_name: string | null; policy_no: string | null
  kind: string; holder: string | null; life_insured: string | null
  sum_assured: number; premium: number; premium_frequency: string
  start_date: string | null; policy_term_years: number | null; premium_paying_term_years: number | null
  return_of_premium: boolean; maturity_date: string | null; surrender_value: number
  /** Lapsed/closed policies stay in the list but are out of every total the endpoint reports. */
  active: boolean
  notes: string | null; annual_premium: number; premiums_paid_so_far: number; maturity_benefit: number
}
export interface InsuranceResponse {
  policies: InsurancePolicy[]; total_cover: number; annual_premium: number; total_surrender_value: number
}
/**
 * The whole policy, every field required. PUT replaces the row column-for-column, so anything left
 * out comes back as its default rather than as it was — optional keys here would hide that.
 */
export type InsuranceInput = {
  provider: string
  plan_name: string | null
  policy_no: string | null
  kind: string
  holder: string | null
  life_insured: string | null
  sum_assured: number
  premium: number
  /** Only "monthly" is special-cased server-side; everything else is read as annual. */
  premium_frequency: string
  start_date: string | null
  policy_term_years: number | null
  premium_paying_term_years: number | null
  return_of_premium: boolean
  maturity_date: string | null
  surrender_value: number | null
  active: boolean
  notes: string | null
}
export async function fetchInsurance(): Promise<InsuranceResponse> {
  const { data } = await api.get<InsuranceResponse>('/insurance'); return data
}
export async function createInsurance(body: InsuranceInput): Promise<InsurancePolicy> {
  const { data } = await api.post<InsurancePolicy>('/insurance', body); return data
}
export async function updateInsurance(id: number, body: InsuranceInput): Promise<InsurancePolicy> {
  const { data } = await api.put<InsurancePolicy>(`/insurance/${id}`, body); return data
}
export async function deleteInsurance(id: number): Promise<void> { await api.delete(`/insurance/${id}`) }

// ---------- unified net worth (assets + investments − liabilities) ----------
export interface NetWorthLine { name: string; value: number; holder: string | null; meta: string }
export interface NetWorthCategory { category: string; value: number; items: NetWorthLine[] }
export interface NetWorthSummary {
  net_worth: number
  total_assets: number
  total_liabilities: number
  categories: NetWorthCategory[]
  liabilities: NetWorthLine[]
  /** Same endpoint has always returned it; the type simply never said so. */
  gold_collateral: GoldCollateral
}
export async function fetchNetWorthSummary(): Promise<NetWorthSummary> {
  const { data } = await api.get<NetWorthSummary>('/networth/summary')
  return data
}

/**
 * Net worth cut by the NATURE of what is held, not by the account holding it — the five bands the
 * Wealth tab draws its proportion bar from. `live_priced` is the honesty flag: false means the
 * figure is accrued or marked, not a price anyone would pay today.
 */
export interface NetWorthBucket {
  key: string
  label: string
  sublabel: string
  count: number
  value_inr: number
  pct: number
  live_priced: boolean
}
export interface NetWorthComposition {
  as_of: string
  net_worth: number
  total_assets: number
  total_liabilities: number
  /** Share of the ASSET side carrying an estimated value rather than a live price. */
  estimated_pct: number
  buckets: NetWorthBucket[]
}
export async function fetchNetWorthComposition(): Promise<NetWorthComposition> {
  const { data } = await api.get<NetWorthComposition>('/networth/composition')
  return data
}
export type RecurringInput = {
  name: string
  kind: string
  purpose?: RecurringPurpose
  holder?: string | null
  opening_balance?: number
  as_of_date: string
  monthly_contribution?: number
  employer_contribution?: number
  annual_rate_pct?: number | null
  compounding?: string | null
  maturity_date?: string | null
  active?: boolean
  from_takehome?: boolean
  scheme_code?: string | null
  units?: number | null
  current_value?: number | null
  notes?: string | null
}
export async function fetchRecurring(refresh = false): Promise<RecurringResponse> {
  const { data } = await api.get<RecurringResponse>('/recurring', { params: { refresh } })
  return data
}
export async function createRecurring(body: RecurringInput): Promise<RecurringInvestment> {
  const { data } = await api.post('/recurring', body)
  return data
}
export async function updateRecurring(id: number, body: RecurringInput): Promise<RecurringInvestment> {
  const { data } = await api.put(`/recurring/${id}`, body)
  return data
}
export async function contributeRecurring(
  id: number,
  /* funded_from defaults to bank server-side, as with loan payments: a contribution made without
     choosing must never quietly drain the wallet. Send EITHER amount or units (units needs a
     scheme_code); negative on either one records a redemption. */
  body: { amount?: number; units?: number; date: string; note?: string; funded_from?: string },
): Promise<RecurringInvestment> {
  const { data } = await api.post(`/recurring/${id}/contribute`, body)
  return data
}
/** Remove one ledger entry by its position in `contributions` (the array the API returned). */
export async function deleteContribution(id: number, index: number): Promise<RecurringInvestment> {
  const { data } = await api.delete(`/recurring/${id}/contribute/${index}`)
  return data
}
export interface RecurringScheme { scheme_code: string; scheme_name: string }
export async function searchSchemes(q: string): Promise<RecurringScheme[]> {
  const { data } = await api.get<{ schemes: RecurringScheme[] }>('/recurring/schemes', { params: { q } })
  return data.schemes ?? []
}
export async function deleteRecurring(id: number): Promise<void> {
  await api.delete(`/recurring/${id}`)
}

// ---------- accounts & cards (user-managed; replaces the old hardcoded registry) ----------
export interface AccountRow {
  id: number
  bank: string
  kind: 'card' | 'account'
  last4: string
  holder: string | null
  label: string | null
  sms_senders: string | null
  active: boolean
  // The statement schedule travels with the account, so a list already fetched can render it
  // without a second call. Optional because POST/PATCH /accounts echo a row built the same way.
  statement_sender?: string | null
  statement_day?: number | null
  statement_run_day?: number | null
  statement_subject_hint?: string | null
  statement_last_imported?: string | null
}
export interface UnknownSender {
  sender: string
  suggested_token: string
  count: number
  last_seen: string
  sample: string
}
/** One inferred statement rhythm. `stable: false` is still worth showing — see discoverStatementSchedule. */
export interface StatementSender {
  sender: string
  shape: string
  day: number
  run_day: number
  samples: number
  last_seen: string
  /** How much the day of the month wobbles across the samples. 0 = same date every time. */
  spread: number
  stable: boolean
  subject: string
  looks_like_card: boolean
}
export interface StatementSchedule {
  id: number
  statement_sender: string | null
  statement_day: number | null
  statement_run_day: number | null
  statement_last_imported: string | null
}
export type AccountInput = Omit<AccountRow, 'id'>

export async function fetchAccounts(): Promise<{ accounts: AccountRow[] }> {
  const { data } = await api.get<{ accounts: AccountRow[] }>('/accounts'); return data
}
export async function createAccount(body: Partial<AccountInput>): Promise<AccountRow> {
  const { data } = await api.post<AccountRow>('/accounts', body); return data
}
export async function updateAccount(id: number, body: Partial<AccountInput>): Promise<AccountRow> {
  const { data } = await api.patch<AccountRow>(`/accounts/${id}`, body); return data
}
export async function deleteAccount(id: number): Promise<void> { await api.delete(`/accounts/${id}`) }
export async function fetchUnknownSenders(): Promise<{ unknown: UnknownSender[]; available: boolean }> {
  const { data } = await api.get<{ unknown: UnknownSender[]; available: boolean }>('/accounts/unknown-senders')
  return data
}
/** Reads the mailbox, so it is slow and deliberate — call it on intent, never on mount.
 *  `available: false` with a `reason` is the ordinary answer when Gmail isn't connected, not a failure. */
export async function discoverStatementSchedule(days = 190):
Promise<{ available: boolean; senders: StatementSender[]; reason?: string }> {
  const { data } = await api.get<{ available: boolean; senders: StatementSender[]; reason?: string }>(
    '/accounts/statement-schedule/discover', { params: { days } })
  return data
}
/** Returns only the schedule fields, so refetch the accounts list rather than merging this into a row. */
export async function setStatementSchedule(id: number, body: {
  statement_sender?: string | null
  statement_day?: number | null
  statement_run_day?: number | null
}): Promise<StatementSchedule> {
  const { data } = await api.patch<StatementSchedule>(`/accounts/${id}/statement-schedule`, body)
  return data
}

// ---------- what the parsers can read ----------
export interface SupportedBank { bank: string; statement: string; accepts: string[] }
export async function fetchSupported(): Promise<{ accepted_file_types: string[]; banks: SupportedBank[] }> {
  const { data } = await api.get<{ accepted_file_types: string[]; banks: SupportedBank[] }>('/supported')
  return data
}

// ---------- member identity (write-only: values go in, only flags come back) ----------
export interface MemberRow {
  id: number
  name: string
  key: string
  is_household: boolean
  has_dob?: boolean
  has_mobile?: boolean
  has_pan?: boolean
  has_pincode?: boolean
  identity_complete?: boolean
  // Fields onboarding insists on. Policy is decided server-side so one place defines what blocks.
  identity_missing_required?: string[]
  identity_optional_missing?: string[]
}
export async function fetchMembers(): Promise<{
  household: { id: number; name: string }
  /** Why each identity field is asked for, keyed by field. Server-owned: the same sentences appear
   *  on the profile screen, and when the component held its own copy the two drifted apart. */
  unlocks: Record<string, string>
  members: MemberRow[]
}> {
  const { data } = await api.get('/members'); return data
}
export async function updateMemberIdentity(
  id: number,
  body: { dob?: string; mobile?: string; pan?: string; pincode?: string },
): Promise<MemberRow> {
  const { data } = await api.patch<MemberRow>(`/members/${id}/identity`, body); return data
}

// ---------- household settings ----------
export interface HouseholdSettings {
  // The stored choice (null = never chosen) sits next to what the engines actually use, so the screen
  // can say "not set, defaulting to X" instead of dressing a default up as a decision.
  tax_regime: 'old' | 'new' | null
  tax_regime_effective: 'old' | 'new'
  budget_baseline_from: string | null
  currency: string | null
  currency_effective: string
}
export async function fetchHouseholdSettings(): Promise<HouseholdSettings> {
  const { data } = await api.get<HouseholdSettings>('/household/settings'); return data
}
export async function updateHouseholdSettings(
  body: Partial<Pick<HouseholdSettings, 'tax_regime' | 'budget_baseline_from' | 'currency'>>,
): Promise<HouseholdSettings> {
  const { data } = await api.put<HouseholdSettings>('/household/settings', body); return data
}

// ---------- windfall (one lump sum, shown as prepay AND invest — never ranked) ----------
export interface WindfallLoan {
  id: number; name: string; loan_type: string; rate_pct: number | null
  balance_inr: number; emi_inr: number | null; applied_inr: number; cleared: boolean
  /** null = still owing at the end of the simulation. 0 = cleared today by the windfall itself. */
  months_before: number | null; months_after: number | null; months_removed: number | null
  interest_before_inr: number; interest_after_inr: number; interest_avoided_inr: number
  interest_avoided_full_term_inr: number
  emi_freed_inr: number; amortises: boolean
}
export interface WindfallScenario {
  key: string; label: string; rate_pct: number
  /** 'observed' = a measurement; 'assumed' = a stated rate, not a forecast. */
  source: 'observed' | 'assumed'
  /**
   * WHO measured it. 'derived_in_app' = Artha worked it out from this household's own holdings.
   * 'entered_by_hand' = measured somewhere Artha cannot see (a broker) and typed in by a person.
   * Those are different claims and must never render alike. null on an assumed rate.
   */
  observed_from: 'derived_in_app' | 'entered_by_hand' | null
  /** The date a hand-entered figure is measured TO, and whether it has gone stale. */
  as_of: string | null
  stale: boolean
  assumption: string; value_inr: number; gain_inr: number
}
/** A return measured somewhere else and typed in. Never derived here, never an assumption. */
export interface WindfallMeasuredReturn {
  rate_pct: number
  scope: string | null
  as_of: string | null
  entered_at: string | null
  as_of_days_ago: number | null
  stale: boolean
  stale_after_days: number
  in_use: boolean
  /** True once Artha can derive an XIRR itself — the derived figure wins, this one stays visible. */
  superseded_by_derived: boolean
  why_not_derived: string | null
}
export interface WindfallResponse {
  amount_inr: number
  amount_source: 'requested' | 'investable_cash'
  investable_cash: {
    amount_inr: number; liquid_cash_inr: number | null; emergency_buffer_inr: number | null
    /** Owed but not arrived, so not deployable. null = not measured. */
    receivable_inr: number | null
    basis: string; data_ok: boolean
  }
  horizon_months: number
  /** 'chosen' = the household picked this window; 'default' = the app's stated one. */
  horizon_source: 'chosen' | 'default'
  horizon_basis: string
  horizon_choices: { months: number; label: string }[]
  prepay: {
    certainty: 'certain'; basis: string; order: string
    horizon_months: number; horizon_note: string
    /** Which monthly surplus produced these numbers, and why that one rather than the other. */
    surplus: {
      assumed_per_month_inr: number; field: string; source: string; why_this_one: string
      surplus_inr: number | null; actionable_inr: number | null; over_budget_inr: number | null
      data_ok: boolean
    }
    /** The payoff plan ALREADY running — the surplus on the debt, no windfall in it. */
    baseline: {
      what: string; with_surplus_inr: number; months: number; complete: boolean
      interest_inr: number; interest_full_term_inr: number
    }
    with_windfall: {
      what: string; months: number; complete: boolean
      interest_inr: number; interest_full_term_inr: number
    }
    amount_applied_inr: number; amount_unused_inr: number; prepayment_fees_inr: number
    interest_avoided_inr: number; interest_avoided_full_term_inr: number
    emi_freed_per_month_inr: number; months_removed: number
    horizon_months_before: number; horizon_months_after: number
    loans: WindfallLoan[]; cleared: string[]; considered: number[]
    excluded: { id: number; name: string; reason: string }[]
    interest_incomplete: string[]
  }
  invest: {
    certainty: 'uncertain'; basis: string; method: string; horizon_months: number
    single_rate_used: boolean
    /** Investing the windfall does not switch the debt off — the surplus keeps clearing it. */
    while_invested: {
      surplus_per_month_inr: number; debt_months: number; debt_complete: boolean; note: string
    }
    measured_return: WindfallMeasuredReturn | null
    scenarios: WindfallScenario[]
    range: {
      low_rate_pct: number; high_rate_pct: number
      low_value_inr: number; high_value_inr: number
      low_gain_inr: number; high_gain_inr: number
    }
    realised_xirr_unavailable_why: string | null
  }
  certainty: {
    prepay: { kind: 'certain'; figure: string; why: string }
    invest: { kind: 'uncertain'; figure: string; why: string }
    comparable: boolean
    comparable_why: string
  }
  disclaimer: string
}

export async function fetchWindfall(
  amount?: number | null,
  horizonMonths?: number | null,
): Promise<WindfallResponse> {
  const params: Record<string, number> = {}
  if (amount != null) params.amount = amount
  // Omitted, never guessed client-side: the app's stated default lives in one place, the API.
  if (horizonMonths != null) params.horizon_months = horizonMonths
  const { data } = await api.get<WindfallResponse>('/windfall', { params })
  return data
}

export interface MeasuredReturnEnvelope {
  measured_return: WindfallMeasuredReturn | null
  stale_after_days: number
}

export async function saveMeasuredReturn(body: {
  rate_pct: number; scope: string; as_of: string
}): Promise<MeasuredReturnEnvelope> {
  const { data } = await api.put<MeasuredReturnEnvelope>('/windfall/measured-return', body)
  return data
}

export async function clearMeasuredReturn(): Promise<MeasuredReturnEnvelope> {
  const { data } = await api.delete<MeasuredReturnEnvelope>('/windfall/measured-return')
  return data
}
