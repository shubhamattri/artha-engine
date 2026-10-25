import { type ReactNode } from 'react'

import { useAuth } from './AuthContext'

/**
 * Hide or disable what the signed-in person cannot use.
 *
 * **This is not security and must never be treated as one.** The server has already decided and
 * will refuse the request whatever this renders. Every route declares its permission in
 * `backend/auth/permissions.py`, the app refuses to start if one does not, and the gate answers 403
 * before the endpoint body runs.
 *
 * The reason to say that so loudly: the system this design reacts to has a data-masking bypass that
 * works because a button writes a timestamp into localStorage and the server believes it. That is
 * what happens when a UI check is allowed to feel like a gate — someone stops adding the server
 * rule, and the client becomes the authority by accident.
 *
 * So the only job here is politeness: do not offer someone a control that will fail.
 */
export function Can({ view, edit, children, otherwise = null }: {
  /** Permission key needed at `view` level. */
  view?: string
  /** Permission key needed at `edit` level. */
  edit?: string
  children: ReactNode
  /** Rendered instead when the person lacks it. Defaults to nothing at all. */
  otherwise?: ReactNode
}) {
  const { can } = useAuth()
  const ok = edit ? can(edit, 'edit') : view ? can(view, 'view') : true
  return <>{ok ? children : otherwise}</>
}

/**
 * For controls that should stay VISIBLE but inert — a disabled Save rather than a vanished one.
 *
 * Worth choosing deliberately per control. A button that disappears can read as a broken screen; a
 * button that is present and disabled says "this exists, but not for you", which is usually the
 * kinder answer for someone who has view access and is wondering why they cannot edit.
 */
export function useCan(key: string, level: 'view' | 'edit' = 'view'): boolean {
  return useAuth().can(key, level)
}

/**
 * The one sentence shown where a block of edit controls would be.
 *
 * Written once because it is the same answer everywhere, and because the alternative — each screen
 * inventing its own phrasing — is how an app ends up saying "Access denied", "Forbidden" and
 * "You do not have permission" on three consecutive screens.
 *
 * It names a person to ask rather than a permission key. Someone who cannot edit their household's
 * loans does not want to read `debt`; they want to know that this is deliberate and who changes it.
 */
export function ViewOnly({ what }: { what: string }) {
  return (
    <p className="rounded-input border border-line bg-surface-2 px-3 py-2 text-[11.5px] leading-relaxed text-ink-2">
      You can see {what} but not change it. Ask whoever administers this household if that is wrong.
    </p>
  )
}
