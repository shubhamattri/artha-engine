import type { ReactNode } from 'react'
import Logo from './ui/Logo'

/** Full-screen auth layout — brand comes from the Logo (config-driven), light Sage theme. */
export default function AuthShell({
  title, subtitle, children,
}: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-bg text-ink">
      {/* brand panel (desktop) */}
      <div className="relative hidden flex-1 flex-col justify-between border-r border-line p-10 md:flex"
        style={{ background: 'linear-gradient(160deg,var(--accent-2),var(--accent))' }}>
        <div className="text-white"><Logo size={40} /></div>
        <p className="max-w-sm text-lg font-medium leading-snug text-white/90">
          The honest money command centre for your household — see where it goes, what you owe, and how to get ahead.
        </p>
      </div>

      {/* form column. The brand panel above is md-only, so on a phone — the primary device, and the
          one an invited household opens the link on — the page said nothing about what this is.
          The same sentence, shown only where the panel is not. */}
      <div className="flex w-full items-center justify-center px-6 md:w-[460px] md:shrink-0">
        <div className="w-full max-w-sm">
          <div className="mb-4 md:hidden"><Logo size={36} /></div>
          <p className="mb-6 text-sm leading-relaxed text-ink-2 md:hidden">
            The honest money command centre for your household — see where it goes, what you owe, and
            how to get ahead.
          </p>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">{title}</h1>
          {subtitle && <p className="mt-2 text-sm leading-relaxed text-ink-2">{subtitle}</p>}
          <div className="mt-8">{children}</div>
        </div>
      </div>
    </div>
  )
}

// Canonical accent button — shared by all auth screens. Same accent/radius as the rest of the app.
export const btnCls =
  'w-full min-h-11 rounded-input bg-accent px-4 text-sm font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-50'
