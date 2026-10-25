import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AuthShell, { btnCls } from '../components/AuthShell'
import { TextInput } from '../components/ui'
import { login } from '../api/auth'

export default function Login() {
  const nav = useNavigate()
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(identifier, password)
      nav('/dashboard', { replace: true })
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not sign in. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell title="Sign in" subtitle="Private to your household. Invite-only — there's no sign-up.">
      <form onSubmit={submit} className="space-y-4">
        <TextInput
          label="Username or email"
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          autoFocus
          autoComplete="username"
          placeholder="username or email"
        />
        <TextInput
          label="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          placeholder="••••••••"
        />

        {error && <div className="text-sm text-negative">{error}</div>}

        <button type="submit" disabled={busy || !identifier || !password} className={btnCls}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="pt-2 text-center text-xs">
          <Link to="/forgot" className="text-ink-2 hover:text-accent">
            Forgot your password?
          </Link>
        </div>
      </form>
    </AuthShell>
  )
}
