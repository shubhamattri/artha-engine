import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import AuthShell, { btnCls } from '../components/AuthShell'
import { TextInput } from '../components/ui'
import { resetPassword } from '../api/auth'

export default function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const nav = useNavigate()

  const [pw, setPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (pw.length < 8) return setError('Password must be at least 8 characters.')
    if (pw !== confirm) return setError('Passwords do not match.')
    setBusy(true)
    try {
      await resetPassword(token, pw)
      setDone(true)
      setTimeout(() => nav('/login', { replace: true }), 1600)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not set the password.')
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <AuthShell title="Set your password">
        <p className="text-sm text-negative">This link is missing its token. Use the link from your email.</p>
        <Link to="/login" className="mt-6 inline-block text-sm text-accent hover:text-accent-2">← Back to sign in</Link>
      </AuthShell>
    )
  }

  return (
    <AuthShell title="Set your password" subtitle="Choose a strong password — at least 8 characters.">
      {done ? (
        <p className="text-sm text-positive">Password set. Taking you to sign in…</p>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <TextInput
            label="New password"
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            autoFocus
            autoComplete="new-password"
            placeholder="••••••••"
          />
          <TextInput
            label="Confirm password"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            placeholder="••••••••"
          />
          {error && <div className="text-sm text-negative">{error}</div>}
          <button type="submit" disabled={busy || !pw || !confirm} className={btnCls}>
            {busy ? 'Saving…' : 'Set password'}
          </button>
        </form>
      )}
    </AuthShell>
  )
}
