import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import {
  type ManagedCategory,
  createCategory,
  deleteCategory,
  fetchAllCategories,
  fetchCategoryRules,
  formatINR,
  mergeCategory,
  orderCategories,
  renameCategory,
  splitCategory,
  updateCategorySettings,
} from '../api/client'
import { ViewOnly, useCan } from '../auth/Can'
import { Card, ErrorState, Icon, Loading, SelectInput, TextInput, useConfirm } from './ui'

/**
 * Every category the household has, in ONE list, with the same controls on each.
 *
 * This screen first shipped listing only categories the household had invented, because those live in
 * a table while the built-in taxonomy is an enum. That is an implementation detail and it reached the
 * user: a household using only built-ins opened it and found nothing, while Groceries and Travel sat
 * on the Plan behind it. Those are the categories people actually use.
 *
 * Exactly one thing differs between the two kinds, and the constraint is real. Forty places in the
 * backend compare against the built-in names and the AI classifier's prompt embeds them as text, so a
 * built-in's stored name is a KEY: renaming it sets a label you see, while the key stays put. A custom
 * category's name is its own identity, so renaming it rewrites the household's data for real.
 * Everything else — colour, target, archive, committed — behaves identically on both.
 */
export default function CategoryManager({ onChanged }: { onChanged?: () => void }) {
  const confirm = useConfirm()
  const q = useQuery({ queryKey: ['all-categories'], queryFn: fetchAllCategories })
  const cats = q.data ?? []
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const mayEdit = useCan('categories', 'edit')

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await q.refetch()
      onChanged?.()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail || 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  if (q.isLoading) return <Loading rows={4} />
  if (q.isError) return <ErrorState message="Couldn’t load your categories." onRetry={() => q.refetch()} />

  const mine = cats.filter((c) => !c.builtin)
  const builtin = cats.filter((c) => c.builtin)

  return (
    <div className="space-y-4">
      {/* Said once, at the top. The list below still reads in full — what each category is called,
          what it costs, what routes into it — with only the controls on each row gone. */}
      {!mayEdit ? <ViewOnly what="your categories" /> : (
      <Card>
        <div className="flex items-end gap-2">
          <TextInput label="Add a category" value={name} placeholder="e.g. Weekend trips"
            onChange={(e) => setName(e.target.value)} />
          <button
            onClick={() => name.trim() && run(async () => { await createCategory(name.trim()); setName('') })}
            disabled={busy || !name.trim()}
            className="inline-flex min-h-11 items-center rounded-input bg-accent px-4 text-sm font-semibold text-white disabled:opacity-40">
            Add
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-negative">{error}</p>}
      </Card>
      )}

      {/* The household's own first, because those are the ones it will touch most, then the built-ins
          it can equally rename, recolour, target, archive or mark committed. */}
      {mine.length > 0 && (
        <Group title="Yours" cats={mine} all={cats}
          openKey={openKey} setOpenKey={setOpenKey} busy={busy} run={run} confirm={confirm} />
      )}
      <Group title={mine.length > 0 ? 'Built in' : 'Your categories'} cats={builtin} all={cats}
        openKey={openKey} setOpenKey={setOpenKey} busy={busy} run={run} confirm={confirm} />
    </div>
  )
}

function Group({ title, cats, all, openKey, setOpenKey, busy, run, confirm }: {
  title: string
  /** The categories in THIS group. */
  cats: ManagedCategory[]
  /** Every category, so "merge into" can offer one from the other group. */
  all: ManagedCategory[]
  openKey: string | null
  setOpenKey: (k: string | null) => void
  busy: boolean
  run: (fn: () => Promise<unknown>) => Promise<void>
  confirm: (o: { title: string; body?: string; confirmLabel?: string; danger?: boolean }) => Promise<boolean>
}) {
  return (
    <div>
      <div className="tracked mb-2 px-1 text-[10px] font-semibold text-ink-3">{title}</div>
      <div className="space-y-2">
        {cats.map((c, i) => (
          <Row key={c.category} cat={c} all={all} busy={busy} run={run} confirm={confirm}
            open={openKey === c.category}
            onToggle={() => setOpenKey(openKey === c.category ? null : c.category)}
            canUp={i > 0} canDown={i < cats.length - 1}
            onMove={(dir) => {
              const names = cats.map((x) => x.category)
              const j = i + dir
              ;[names[i], names[j]] = [names[j], names[i]]
              return run(() => orderCategories(names))
            }} />
        ))}
      </div>
    </div>
  )
}

function Row({ cat, all, open, busy, run, confirm, onToggle, canUp, canDown, onMove }: {
  cat: ManagedCategory
  all: ManagedCategory[]
  open: boolean
  busy: boolean
  run: (fn: () => Promise<unknown>) => Promise<void>
  confirm: (o: { title: string; body?: string; confirmLabel?: string; danger?: boolean }) => Promise<boolean>
  onToggle: () => void
  canUp: boolean
  canDown: boolean
  onMove: (dir: number) => Promise<void>
}) {
  const [label, setLabel] = useState(cat.display_name)
  // Pre-filled from what the Plan actually shows. Falling back to the default only matters for a
  // month that has no row of its own; showing the default first made every row read "no target"
  // while the Plan beside it showed a real figure.
  const [target, setTarget] = useState(
    cat.current_target_inr != null ? String(cat.current_target_inr)
      : cat.default_target_inr != null ? String(cat.default_target_inr) : '')
  const [into, setInto] = useState('')
  const others = all.filter((o) => o.category !== cat.category)
  const mayEdit = useCan('categories', 'edit')
  // Stripped of its controls a built-in row has nothing left inside, so it stops offering to open.
  const expandable = mayEdit || cat.id != null

  async function saveName() {
    const next = label.trim()
    if (!next || next === cat.display_name) return
    // A built-in's stored name is a key forty places compare against, so only the label moves. A
    // custom category's name IS its identity, so this rewrites transactions, targets and rules.
    if (cat.builtin) await run(() => updateCategorySettings(cat.category, { display_name: next }))
    else if (cat.id != null) await run(() => renameCategory(cat.id!, next))
  }

  return (
    <Card padded={false} className="px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="h-3 w-3 flex-none rounded-chip"
          style={{ background: cat.color || 'var(--line-2)' }} />
        <button onClick={onToggle} className="flex min-h-11 flex-1 items-center justify-between gap-2 text-left">
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-ink">{cat.display_name}</span>
            <span className="block text-[11px] text-ink-3">
              {!cat.active && 'archived · '}
              {!cat.is_variable && 'committed · '}
              {cat.display_name !== cat.category && `really ${cat.category} · `}
              {cat.current_target_inr != null
                ? `${formatINR(cat.current_target_inr)} this month`
                : cat.default_target_inr != null
                  ? `${formatINR(cat.default_target_inr)}/mo default`
                  : 'no target'}
            </span>
          </span>
          {expandable && <span className={`text-ink-3 transition-transform ${open ? 'rotate-90' : ''}`}>›</span>}
        </button>
        {mayEdit && (
          <span className="flex flex-none items-center">
            <button onClick={() => canUp && onMove(-1)} disabled={!canUp || busy}
              aria-label={`Move ${cat.display_name} up`}
              className="inline-flex min-h-11 min-w-11 items-center justify-center text-ink-2 disabled:opacity-25">↑</button>
            <button onClick={() => canDown && onMove(1)} disabled={!canDown || busy}
              aria-label={`Move ${cat.display_name} down`}
              className="inline-flex min-h-11 min-w-11 items-center justify-center text-ink-2 disabled:opacity-25">↓</button>
          </span>
        )}
      </div>

      {open && expandable && (
        <div className="mt-3 space-y-4 border-t border-line pt-3">
          {mayEdit && (
          <>
          <div className="flex items-end gap-2">
            <TextInput label={cat.builtin ? 'Call it' : 'Name'} value={label}
              onChange={(e) => setLabel(e.target.value)}
              hint={cat.builtin ? `Shown instead of “${cat.category}”. Clear it to go back.` : undefined} />
            <button onClick={saveName} disabled={busy || !label.trim() || label.trim() === cat.display_name}
              className="inline-flex min-h-11 items-center rounded-input border border-line px-3 text-sm font-semibold text-ink disabled:opacity-40">
              Save
            </button>
          </div>

          <div className="flex items-end gap-2">
            <TextInput label="Monthly target" inputMode="numeric" value={target}
              hint="Applies to this month and seeds the next ones. Blank clears it."
              placeholder="leave blank for none" onChange={(e) => setTarget(e.target.value)} />
            <button
              onClick={() => run(() => updateCategorySettings(cat.category, {
                default_target_inr: target.trim() === '' ? null : Number(target),
              }))}
              disabled={busy}
              className="inline-flex min-h-11 items-center rounded-input border border-line px-3 text-sm font-semibold text-ink disabled:opacity-40">
              Save
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Toggle label={cat.active ? 'Archive' : 'Un-archive'} busy={busy}
              hint={cat.active ? 'off the pickers, history kept' : 'offer it again'}
              onClick={() => run(() => updateCategorySettings(cat.category, { active: !cat.active }))} />
            <Toggle label={cat.is_variable ? 'Mark committed' : 'Mark everyday'} busy={busy}
              hint={cat.is_variable ? 'out of the Plan pool' : 'back into the Plan pool'}
              onClick={() => run(() => updateCategorySettings(cat.category, { is_variable: !cat.is_variable }))} />
            <label className="inline-flex min-h-11 items-center gap-2 text-xs text-ink-2">
              Colour
              <input type="color" value={cat.color || '#0F7A52'}
                onChange={(e) => run(() => updateCategorySettings(cat.category, { color: e.target.value }))}
                className="h-8 w-10 cursor-pointer rounded border border-line bg-transparent" />
            </label>
          </div>
          </>
          )}

          <RulesAndSplit cat={cat} busy={busy} run={run} />

          {/* Merge and delete move a category's PAST into another bucket, so they only make sense for
              one you added. A built-in cannot be removed from the taxonomy; archive it instead. */}
          {mayEdit && !cat.builtin && cat.id != null && (
            <>
              {others.length > 0 && (
                <div className="flex items-end gap-2">
                  <SelectInput label="Merge into" value={into} onChange={(e) => setInto(e.target.value)}>
                    <option value="">Choose a category</option>
                    {others.map((o) => <option key={o.category} value={o.category}>{o.display_name}</option>)}
                  </SelectInput>
                  <button
                    onClick={async () => {
                      if (!into) return
                      const ok = await confirm({
                        title: `Merge “${cat.display_name}” into “${into}”?`,
                        body: 'Every transaction, target and learned merchant rule moves across, and this category is removed. Its past spend will read as if it had always been the other one.',
                        confirmLabel: 'Merge',
                      })
                      if (ok) await run(() => mergeCategory(cat.id!, into))
                    }}
                    disabled={busy || !into}
                    className="inline-flex min-h-11 items-center rounded-input border border-line px-3 text-sm font-semibold text-ink disabled:opacity-40">
                    Merge
                  </button>
                </div>
              )}
              <button
                onClick={async () => {
                  const ok = await confirm({
                    title: `Delete “${cat.display_name}”?`,
                    body: 'Its transactions, targets and rules move to Personal / Misc. If you simply stopped using it, archive instead — that keeps the past saying what actually happened.',
                    confirmLabel: 'Delete',
                    danger: true,
                  })
                  if (ok) await run(() => deleteCategory(cat.id!))
                }}
                disabled={busy}
                className="inline-flex min-h-11 items-center px-1 text-xs font-semibold text-negative disabled:opacity-40">
                Delete this category
              </button>
            </>
          )}
        </div>
      )}
    </Card>
  )
}

function Toggle({ label, hint, busy, onClick }: {
  label: string; hint: string; busy: boolean; onClick: () => void
}) {
  return (
    <button onClick={onClick} disabled={busy}
      className="inline-flex min-h-11 flex-col items-start justify-center rounded-input border border-line px-3 py-1 text-left disabled:opacity-40">
      <span className="text-xs font-semibold text-ink">{label}</span>
      <span className="text-[10px] text-ink-3">{hint}</span>
    </button>
  )
}

/** Which merchants feed this category, and pulling some of them out into a new one. */
function RulesAndSplit({ cat, busy, run }: {
  cat: ManagedCategory; busy: boolean; run: (fn: () => Promise<unknown>) => Promise<void>
}) {
  const [show, setShow] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [newName, setNewName] = useState('')
  // Which merchants land here is worth reading on its own; only pulling some of them out is a change.
  const mayEdit = useCan('categories', 'edit')
  const rules = useQuery({
    queryKey: ['category-rules', cat.category],
    // Rules are read by the category's row id, which only a household's own category has. A built-in
    // has no row, so this stays closed for those rather than showing an endpoint that cannot answer.
    queryFn: () => fetchCategoryRules(cat.id as number),
    enabled: show && cat.id != null,
  })

  if (cat.id == null) return null

  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <button onClick={() => setShow(!show)}
        className="inline-flex min-h-11 items-center gap-2 text-xs font-semibold text-ink-2">
        <Icon name="list" size={14} />
        {show ? 'Hide' : 'Which merchants land here'}
      </button>

      {show && (
        rules.isError ? (
          <ErrorState message="Couldn’t load these rules." onRetry={() => rules.refetch()} />
        ) : !rules.isSuccess ? (
          <p className="mt-2 text-xs text-ink-3">Loading…</p>
        ) : rules.data.length === 0 ? (
          <p className="mt-2 text-xs text-ink-3">
            Nothing is routed here yet. Merchants land in a category when you confirm one on a
            transaction, or when the classifier decides.
          </p>
        ) : (
          <div className="mt-2 space-y-2">
            {rules.data.map((r) => (
              <label key={r.id} className="flex min-h-11 items-center gap-3 text-xs">
                {/* The tick only exists to choose what to split out, so it goes with the split. */}
                {mayEdit && (
                  <input type="checkbox" className="h-4 w-4 shrink-0 accent-accent"
                    checked={picked.includes(r.signature)}
                    onChange={(e) => setPicked(e.target.checked
                      ? [...picked, r.signature]
                      : picked.filter((s) => s !== r.signature))} />
                )}
                <span className="min-w-0 flex-1 truncate text-ink">{r.merchant || r.signature}</span>
                {/* kind answers "why is this here": you confirmed it, or a model guessed. */}
                <span className="shrink-0 text-[10px] text-ink-3">{r.kind} · {r.hits} seen</span>
              </label>
            ))}

            {picked.length > 0 && (
              <div className="flex items-end gap-2 border-t border-line pt-2">
                <TextInput label={`Split ${picked.length} out into`} value={newName}
                  placeholder="new category name" onChange={(e) => setNewName(e.target.value)} />
                <button
                  onClick={() => newName.trim() && run(async () => {
                    await splitCategory(cat.id as number, newName.trim(), picked)
                    setPicked([])
                    setNewName('')
                  })}
                  disabled={busy || !newName.trim()}
                  className="inline-flex min-h-11 items-center rounded-input bg-accent px-3 text-sm font-semibold text-white disabled:opacity-40">
                  Split
                </button>
              </div>
            )}
          </div>
        )
      )}
    </div>
  )
}
