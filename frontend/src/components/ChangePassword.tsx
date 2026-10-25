import { useState } from 'react'

import { changePassword } from '../api/auth'
import { Card, SectionHeader, TextInput } from './ui'

/**
 * Set a new password here, in the app. No email, no reset link.
 *
 * The email route could never work on this deployment — no SMTP host is configured and the app is
 * reached over a private tailnet, not a public URL — so "we sent you a link" was a promise the server
 * had no way to keep. A household changing its own password does not need a mail round-trip.
 *
 * The current password is the proof of ownership. A session cookie is not: an unlocked phone left on
 * a table carries one. That also means this cannot help someone already locked OUT — that case is
 * `scripts/reset_password.py` on the server, where being able to run the command is the authentication.
 */
export default function ChangePassword() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')

  const tooShort = next.length > 0 && next.length < 8
  const mismatch = repeat.length > 0 && next !== repeat
  const ready = current.length > 0 && next.length >= 8 && next === repeat && !busy

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    setDone('')
    try {
      setDone(await changePassword(current, next))
      // Clear on success so the values do not sit in a form (or in a screenshot) afterwards.
      setCurrent(''); setNext(''); setRepeat('')
    } catch (err: unknown) {
      // Say what the server said. The failure that matters here is "that is not your current
      // password", and a generic "something went wrong" would leave you guessing which field is wrong.
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail || 'That didn’t go through. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <SectionHeader level={2} title="Password"
        sub="Change it here. This app doesn’t email reset links." />
      <Card>
        <form onSubmit={submit} className="space-y-4">
          <TextInput label="Current password" type="password" value={current}
            autoComplete="current-password" placeholder="the one you use now"
            onChange={(e) => setCurrent(e.target.value)} />
          <TextInput label="New password" type="password" value={next}
            autoComplete="new-password" placeholder="at least 8 characters"
            error={tooShort ? 'At least 8 characters.' : undefined}
            onChange={(e) => setNext(e.target.value)} />
          <TextInput label="New password again" type="password" value={repeat}
            autoComplete="new-password" placeholder="type it once more"
            error={mismatch ? 'These two don’t match.' : undefined}
            onChange={(e) => setRepeat(e.target.value)} />

          {error && <p className="text-sm text-negative">{error}</p>}
          {done && <p className="text-sm text-positive">{done}</p>}

          <button type="submit" disabled={!ready}
            className="inline-flex min-h-11 items-center rounded-input bg-accent px-4 text-sm font-semibold text-white disabled:opacity-40">
            {busy ? 'Changing…' : 'Change password'}
          </button>
        </form>
      </Card>
    </div>
  )
}
