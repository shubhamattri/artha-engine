import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import Sheet from './Sheet'

type ConfirmOpts = { title: string; message?: string; confirmLabel?: string; danger?: boolean }
type ConfirmFn = (opts: ConfirmOpts) => Promise<boolean>

const ConfirmCtx = createContext<ConfirmFn>(async () => false)
/** Branded async confirm — replaces window.confirm (which breaks the installed-PWA feel). */
export const useConfirm = () => useContext(ConfirmCtx)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ opts: ConfirmOpts; resolve: (v: boolean) => void } | null>(null)

  const confirm = useCallback<ConfirmFn>(
    (opts) => new Promise<boolean>((resolve) => setState({ opts, resolve })),
    [],
  )
  const settle = (v: boolean) => { state?.resolve(v); setState(null) }

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      <Sheet open={!!state} onClose={() => settle(false)} title={state?.opts.title}>
        {state?.opts.message && <p className="text-sm leading-relaxed text-ink-2">{state.opts.message}</p>}
        <div className="mt-5 flex gap-3">
          <button
            onClick={() => settle(false)}
            className="min-h-11 flex-1 rounded-xl border border-line text-sm font-semibold text-ink hover:border-line-2"
          >
            Cancel
          </button>
          <button
            onClick={() => settle(true)}
            className={`min-h-11 flex-1 rounded-xl text-sm font-semibold text-white ${
              state?.opts.danger ? 'bg-negative hover:opacity-90' : 'bg-accent hover:bg-accent-2'
            }`}
          >
            {state?.opts.confirmLabel ?? 'Confirm'}
          </button>
        </div>
      </Sheet>
    </ConfirmCtx.Provider>
  )
}
