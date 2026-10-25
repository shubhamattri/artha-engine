import Icon, { type IconName } from './Icon'

/**
 * The four data-view states, standardized. Every list/query renders one of these instead of
 * the old `if (isLoading || !data) return 'Loading…'` (which hid errors forever).
 */

export function Loading({ rows = 3, label }: { rows?: number; label?: string }) {
  return (
    <div className="space-y-3" role="status" aria-busy="true" aria-label={label ?? 'Loading'}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-14 rounded-2xl border border-line bg-surface-2 animate-pulse" />
      ))}
    </div>
  )
}

export function EmptyState({
  icon = 'list', title, hint, action,
}: {
  icon?: IconName
  title: string
  hint?: string
  action?: { label: string; onClick: () => void }
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-surface-2 px-6 py-12 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-accent-soft text-accent">
        <Icon name={icon} size={22} />
      </span>
      <div className="mt-3 text-sm font-semibold text-ink">{title}</div>
      {hint && <div className="mt-1 max-w-xs text-sm text-ink-2">{hint}</div>}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-4 min-h-11 rounded-xl bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-2"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-line bg-surface px-6 py-10 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-full text-negative" style={{ background: '#C0533B14' }}>
        <Icon name="alert" size={22} />
      </span>
      <div className="mt-3 text-sm font-semibold text-ink">Couldn’t load this</div>
      <div className="mt-1 max-w-xs text-sm text-ink-2">{message ?? 'Something went wrong fetching your data.'}</div>
      {onRetry && (
        <button onClick={onRetry} className="mt-4 min-h-11 rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:border-line-2">
          Try again
        </button>
      )}
    </div>
  )
}
