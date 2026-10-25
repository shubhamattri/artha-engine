import { useQuery } from '@tanstack/react-query'
import { api, fetchHouseholdSettings, setDisplayCurrency } from './client'

// Product config + household members — so the UI renders ZERO hardcoded brand/holder strings.
/** Branding and formatting only, and genuinely public: the login screen renders the wordmark
 *  before a session exists. The cycle fields this used to carry came from the APP defaults, not the
 *  household, so a household on its own anchor day saw the wrong one. They live on
 *  `useHouseholdCycle` now. */
export interface AppConfig {
  brand_name: string
  brand_wordmark: string
  currency: string
  currency_symbol: string
  locale: string
}
/** This household's billing cycle, from the household. Requires a session. */
export interface HouseholdCycle {
  cycle_start_day: number
  is_default: boolean
  start: string
  end_exclusive: string
  days: number
  per_card_cycles: boolean                 // false = ONE window for every card/account
  card_cycle_days: Record<string, number>  // only populated when per_card_cycles is true
  year_start_month: number
  year_is_default: boolean
  current_year: string
}
export interface Member { id: number; name: string; key: string; is_household: boolean }

export async function fetchConfig(): Promise<AppConfig> {
  const { data } = await api.get<AppConfig>('/config')
  return data
}
export async function fetchMembers(): Promise<{ household: { id: number; name: string }; members: Member[] }> {
  const { data } = await api.get('/members')
  return data
}

/** App brand/currency config (public; cached for the session). */
export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: fetchConfig, staleTime: Infinity })
}

export async function fetchHouseholdCycle(): Promise<HouseholdCycle> {
  const { data } = await api.get<HouseholdCycle>('/household/cycle')
  return data
}
/**
 * Set the anchor day, and optionally the month the financial year starts in.
 *
 * The body is asymmetric and the asymmetry bites: `cycle_start_day` is read on EVERY call, so
 * omitting it sets it to null, but `year_start_month` is only touched when the key is present.
 * Changing just the year start therefore has to resend the day it did not want to change — hence
 * `cycle_start_day` being required here rather than optional.
 */
export async function setHouseholdCycle(body: {
  cycle_start_day: number | null
  year_start_month?: number | null
}): Promise<HouseholdCycle> {
  const { data } = await api.put<HouseholdCycle>('/household/cycle', body)
  return data
}

/** The household's own cycle. Separate from useConfig because this one needs to know who is asking:
 *  reading it from the app defaults is what made the Spending tab say "the 12th to the 12th" for a
 *  household whose cycle starts on the 13th. */
export function useHouseholdCycle() {
  return useQuery({ queryKey: ['household-cycle'], queryFn: fetchHouseholdCycle, staleTime: 300_000 })
}

/** Points the money formatters at the household's currency. Mount ONCE (Layout) — it shares the
 *  'household-settings' query, so no component pays for its own fetch. */
export function useDisplayCurrency() {
  const cfg = useConfig()
  const hh = useQuery({
    queryKey: ['household-settings'], queryFn: fetchHouseholdSettings,
    staleTime: 300_000, retry: false,
  })
  // currency_effective already resolves household → app default on the server.
  const currency = hh.data?.currency_effective ?? cfg.data?.currency
  setDisplayCurrency(currency, cfg.data?.locale)
  return currency ?? 'INR'
}

/** Household members — the single source for holder pickers (replaces a hardcoded name list). */
export function useHolders() {
  const q = useQuery({ queryKey: ['members'], queryFn: fetchMembers, staleTime: 300_000 })
  return { ...q, members: q.data?.members ?? [], holders: (q.data?.members ?? []).map((m) => m.key) }
}

/**
 * The cross-module invariants, run against this household's live data.
 *
 * Artha's modules were each built in their own tab and every one is correct in isolation. These are
 * the assertions that they still agree with each other — that the weekly bars sum to the plan, that
 * both net-worth queries return the same assets, that leftover closes the income waterfall.
 *
 * `error` means a figure on some screen is materially wrong right now. `warn` is a fact worth
 * knowing that is not a defect.
 */
export interface ConsistencyFinding {
  check: string
  severity: 'error' | 'warn' | 'ok'
  /** The module that OWNS the number, so a disagreement has an address. */
  owner: string
  detail: string
  expected: number | null
  actual: number | null
  gap: number | null
  fix: string | null
}

export interface ConsistencyReport {
  ok: boolean
  error_count: number
  warn_count: number
  findings: ConsistencyFinding[]
}

export async function fetchConsistency(): Promise<ConsistencyReport> {
  const { data } = await api.get<ConsistencyReport>('/consistency')
  return data
}
