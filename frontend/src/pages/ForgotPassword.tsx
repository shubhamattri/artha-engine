import { useState } from 'react'
import { Link } from 'react-router-dom'
import AuthShell, { btnCls } from '../components/AuthShell'
import { TextInput } from '../components/ui'
import { forgotPassword } from '../api/auth'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const msg = await forgotPassword(email)
      setMessage(msg)
    } catch {
      // A FAILED request must not report the happy path. Not revealing whether an email is
      // registered is about the outcome for a REQUEST THAT SUCCEEDED; when the call itself did not
      // get through, "a reset link has been sent" is simply false, and it is false in the direction
      // that leaves you waiting for mail nobody tried to send.
      setError('That didn’t go through. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell title="Reset password" subtitle="If this server has email set up, we'll send you a secure link.">
      {message ? (
        <div className="space-y-6">
          <p className="text-sm leading-relaxed text-ink-2">{message}</p>
          <Link to="/login" className="inline-block text-sm text-accent hover:text-accent-2">
            ← Back to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <TextInput
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
            placeholder="you@example.com"
          />
          {error && <p className="text-sm text-negative">{error}</p>}
          <button type="submit" disabled={busy || !email} className={btnCls}>
            {busy ? 'Sending…' : 'Send reset link'}
          </button>
          <div className="pt-2 text-center text-xs">
            <Link to="/login" className="text-ink-2 hover:text-accent">
              ← Back to sign in
            </Link>
          </div>
        </form>
      )}
    </AuthShell>
  )
}
