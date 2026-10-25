import { createContext, useContext, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchMe, type AuthUser } from '../api/auth'

/**
 * The one place the frontend knows who it is.
 *
 * There were three separate fetches of the same thing on three different query keys — `['me']` in
 * RequireAuth, `['me']` in Layout, and `['auth','me']` in Profile, which is a different key and so a
 * second round trip for identical data. None of them passed the user down, so nothing could be
 * gated on it.
 *
 * **What this is not.** Hiding a button is a courtesy, not a gate. The server has already decided
 * and will refuse the request whatever the UI renders. If you find yourself reaching for a check
 * here to keep something safe, the check belongs in `auth/permissions.py`.
 */
export type Level = 0 | 1 | 2      // none | view | edit — mirrors auth.roles.Level

export interface Auth {
  user: AuthUser | null
  isLoading: boolean
  /** `edit` implies `view`, exactly as the server resolves it. */
  can: (key: string, level?: 'view' | 'edit') => boolean
  isAdmin: boolean
  /** Only the super admin manages people. Deliberately separate from `isAdmin`. */
  managesUsers: boolean
}

const AuthCtx = createContext<Auth>({
  user: null, isLoading: true, can: () => false, isAdmin: false, managesUsers: false,
})

const NEEDED: Record<'view' | 'edit', Level> = { view: 1, edit: 2 }

export function AuthProvider({ children }: { children: ReactNode }) {
  // `retry: false` because a 401 here means "not signed in", which is an answer rather than a
  // failure worth retrying three times before the login screen appears.
  const q = useQuery({ queryKey: ['auth', 'me'], queryFn: fetchMe, retry: false, staleTime: 60_000 })
  const user = q.data ?? null

  const value: Auth = {
    user,
    isLoading: q.isLoading,
    // Reads the RESOLVED map the server sent. It deliberately does not know what a role implies —
    // re-deriving that here is how the UI and the server start disagreeing.
    can: (key, level = 'view') => (user?.permissions?.[key] ?? 0) >= NEEDED[level],
    isAdmin: user?.role === 'admin' || user?.role === 'super_admin',
    managesUsers: user?.role === 'super_admin',
  }
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export function useAuth(): Auth {
  return useContext(AuthCtx)
}
