import { Navigate, Outlet } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import Logo from './ui/Logo'

/** Gate for protected routes: is there a session at all.
 *
 *  Route-level and all-or-nothing, deliberately. WHAT a signed-in person may do is the server's
 *  answer, delivered per request; this only decides whether to show the app or the login screen.
 *
 *  Reads the shared context rather than fetching. There used to be three fetches of the same
 *  identity on two different query keys — here, in Layout, and in Profile — and none of them passed
 *  the user down, so nothing could be gated on it.
 */
export default function RequireAuth() {
  const { user, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <Logo size={40} />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return <Outlet />
}
