import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  fetchHouseholdSettings, updateHouseholdSettings, type HouseholdSettings as Settings,
} from '../api/client'
import {
  Card, ErrorState, Field, FormRow, Icon, Loading, SectionHeader, SelectInput, Stat, StatGrid,
  SubmitButton, TextInput, inputClass,
} from './ui'
import { useConfig, useHouseholdCycle, setHouseholdCycle } from '../api/meta'
import { useHouseholdRefresh } from '../hooks/useHouseholdRefresh'
import { ViewOnly, useCan } from '../auth/Can'

/**
 * Household-wide settings, set here rather than by someone running a script against the database —
 * which is how all three of these were set the first time, and works for exactly one household.
 *
 * Each field says what it changes, because none of the three is self-explanatory: the regime decides
 * whether loan deductions exist at all, and the tracking-start date decides which months are allowed
 * to inform a suggested target.
 */
const REGIMES: { value: 'old' | 'new'; label: string; what: string }[] = [
  { value: 'old', label: 'Old regime',
    what: 'Deductions apply — 80C, 80E and home-loan interest cut what a loan really costs you.' },
  { value: 'new', label: 'New regime',
    what: 'Lower slabs, no deductions. A loan costs its headline rate, with nothing back at filing.' },
]

const TODAY = () => new Date().toISOString().slice(0, 10)

export default function HouseholdSettings() {
  const refresh = useHouseholdRefresh()
  const mayEdit = useCan('household.admin', 'edit')
  const brand = useConfig().data?.brand_name ?? 'this app'
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['household-settings'], queryFn: fetchHouseholdSettings,
  })

  const [regime, setRegime] = useState<'old' | 'new' | ''>('')
  const [currency, setCurrency] = useState('')
  const [saved, setSaved] = useState(false)

  // Seed the form from the server once it answers, so the screen shows what is actually stored
  // instead of blanks that would read as "nothing is set".
  useEffect(() => {
    if (!data) return
    setRegime(data.tax_regime ?? '')
    setCurrency(data.currency ?? '')
  }, [data])

  // Sends all three every time, with null for "cleared". The endpoint treats an omitted field as
  // untouched, so a cleared value has to travel as an explicit null or it would never clear.
  const save = useMutation<Settings>({
    mutationFn: () => updateHouseholdSettings({
      tax_regime: regime === '' ? null : regime,
      currency: currency.trim() === '' ? null : currency.trim().toUpperCase(),
    }),
    onSuccess: () => { setSaved(true); refetch(); refresh() },
  })

  if (isLoading) return <Loading />
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />

  const dirty =
    regime !== (data.tax_regime ?? '') ||
    currency.trim().toUpperCase() !== (data.currency ?? '')

  return (
    <div className="space-y-4">
      <SectionHeader
        level={2}
        title="Household settings"
        sub={mayEdit
          ? 'What the whole app reads. Change it here — nobody should have to edit the database.'
          : 'What the whole app reads.'}
      />

      <StatGrid min="11rem">
        <Stat label="Tax regime in effect" value={data.tax_regime_effective === 'old' ? 'Old' : 'New'}
          sub={data.tax_regime ? 'Your choice' : 'Default — not set yet'} />
        <Stat label="Currency" value={data.currency_effective}
          sub={data.currency ? 'Your choice' : 'App default — not set yet'} />
      </StatGrid>

      {!mayEdit ? <ViewOnly what="the household's settings" /> : (
      <Card>
        <FormRow
          cols={2}
          onSubmit={(e) => { e.preventDefault(); setSaved(false); save.mutate() }}
          submit={<SubmitButton disabled={!dirty || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save settings'}
          </SubmitButton>}
        >
          <Field
            label="Income tax regime"
            hint="Decides whether 80C, 80E and home-loan interest relief exist, and therefore whether a loan's headline rate is its real cost."
          >
            <div className="space-y-2">
              {REGIMES.map((r) => (
                <label key={r.value}
                  className={`flex cursor-pointer gap-3 rounded-xl border p-3 transition-colors ${
                    regime === r.value ? 'border-accent bg-surface' : 'border-line bg-surface-2'
                  }`}>
                  <input
                    type="radio"
                    name="tax-regime"
                    value={r.value}
                    checked={regime === r.value}
                    onChange={() => setRegime(r.value)}
                    className="mt-1 h-4 w-4 shrink-0 accent-accent"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-ink">{r.label}</span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">{r.what}</span>
                  </span>
                </label>
              ))}
              {regime !== '' && (
                <button type="button" onClick={() => setRegime('')}
                  className="inline-flex min-h-11 items-center text-xs font-semibold text-ink-3 underline underline-offset-2 hover:text-accent">
                  Clear — go back to the default ({data.tax_regime_effective === 'old' ? 'Old' : 'New'})
                </button>
              )}
            </div>
          </Field>

          <div className="space-y-3">
            <Field
              label="Currency"
              hint={`3-letter code. Leave empty to use the app default (${data.currency_effective}).`}
            >
              <input
                className={inputClass}
                value={currency}
                maxLength={3}
                placeholder={data.currency_effective}
                onChange={(e) => setCurrency(e.target.value.toUpperCase())}
              />
            </Field>
          </div>
        </FormRow>

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
      </Card>
      )}

      <CycleSettings mayEdit={mayEdit} />
    </div>
  )
}

const DAYS = Array.from({ length: 31 }, (_, i) => i + 1)
const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1)

/** Reads an ISO date as a local calendar day — `new Date('2026-08-20')` is UTC midnight, which is
 *  the previous day in any negative offset and would show the window off by one. */
const asDate = (iso: string) => new Date(`${iso}T00:00:00`)

/**
 * The billing-cycle anchor and the financial-year start — read all over the app, and until now
 * settable only by editing the database.
 */
function CycleSettings({ mayEdit }: { mayEdit: boolean }) {
  const refresh = useHouseholdRefresh()
  const locale = useConfig().data?.locale
  const { data, isLoading, isError, refetch } = useHouseholdCycle()

  const [day, setDay] = useState('')
  const [month, setMonth] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!data) return
    setDay(data.is_default ? '' : String(data.cycle_start_day))
    setMonth(data.year_is_default ? '' : String(data.year_start_month))
  }, [data])

  // cycle_start_day is read on EVERY request — omit it and the server sets it to null — while
  // year_start_month is only touched when its key is present. So both always travel together.
  const save = useMutation({
    mutationFn: () => setHouseholdCycle({
      cycle_start_day: day === '' ? null : Number(day),
      year_start_month: month === '' ? null : Number(month),
    }),
    onSuccess: () => { setSaved(true); refetch(); refresh() },
  })

  if (isLoading) return <Loading rows={2} />
  if (isError || !data) return <ErrorState onRetry={() => refetch()} />

  const monthName = (m: number) =>
    new Date(2000, m - 1, 1).toLocaleDateString(locale, { month: 'long' })
  const dayLabel = (iso: string) =>
    asDate(iso).toLocaleDateString(locale, { day: 'numeric', month: 'short' })
  const lastDay = () => {
    const d = asDate(data.end_exclusive)
    d.setDate(d.getDate() - 1)
    return d.toLocaleDateString(locale, { day: 'numeric', month: 'short' })
  }

  const dirty =
    day !== (data.is_default ? '' : String(data.cycle_start_day)) ||
    month !== (data.year_is_default ? '' : String(data.year_start_month))

  return (
    <div className="space-y-4 pt-2">
      <SectionHeader
        level={2}
        title="Billing cycle"
        sub="Banks do not bill on the 1st. Until the app knows which day your statement period starts, every spending total it shows covers the calendar month instead of your actual bill — so the numbers never match the statement you get. Set that day here and the whole app follows it."
      />

      <StatGrid min="11rem">
        <Stat label="Cycle starts on" value={`Day ${data.cycle_start_day}`}
          sub={data.is_default ? 'Default — not set yet' : 'Your choice'} />
        <Stat label="Cycle running now" value={`${dayLabel(data.start)} – ${lastDay()}`}
          sub={`${data.days} days`} />
        <Stat label="Financial year" value={data.current_year}
          sub={`${data.year_is_default ? 'Default' : 'Your choice'} — starts in ${monthName(data.year_start_month)}`} />
      </StatGrid>

      {!mayEdit ? <ViewOnly what="the household's billing cycle" /> : (
      <Card>
        <FormRow
          cols={2}
          onSubmit={(e) => { e.preventDefault(); setSaved(false); save.mutate() }}
          submit={<SubmitButton disabled={!dirty || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save cycle'}
          </SubmitButton>}
        >
          <Field
            label="Statement period starts on"
            hint="The day of the month your bill runs from. Pick 31 for a card that bills at month end — short months fall back to their last day."
          >
            <SelectInput value={day} onChange={(e) => setDay(e.target.value)}>
              <option value="">Use the app default</option>
              {DAYS.map((d) => <option key={d} value={d}>Day {d}</option>)}
            </SelectInput>
          </Field>

          <Field
            label="Financial year starts in"
            hint="Only changes how yearly totals are grouped. Nothing about a month or a cycle moves."
          >
            <SelectInput value={month} onChange={(e) => setMonth(e.target.value)}>
              <option value="">Use the app default</option>
              {MONTHS.map((m) => <option key={m} value={m}>{monthName(m)}</option>)}
            </SelectInput>
          </Field>
        </FormRow>

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
            <Icon name="check" size={16} /> Saved. Every spending total now uses this window.
          </div>
        )}
      </Card>
      )}
    </div>
  )
}
