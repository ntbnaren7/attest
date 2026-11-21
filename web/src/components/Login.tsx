import { useState, type FormEvent } from 'react'
import { api } from '../api'
import { usePrefs } from '../prefs'
import { Mark } from './Status'
import { LangSelect } from './TopBar'

/** Fallback only: shown when the dashboard is opened from another machine (demo sign-in is localhost-only). */
export function Login({ onDone }: { onDone: () => void }) {
  const { t } = usePrefs()
  const [username, setUsername] = useState('meena')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.login(username, password)
      onDone()
    } catch {
      setError(t.wrongLogin)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <form onSubmit={submit}>
        <div className="wordmark" style={{ fontSize: 20 }}>
          <Mark size={24} /> Invariant
        </div>
        <p className="muted" style={{ margin: 0 }}>{t.signInHint}</p>
        <label>
          {t.username}
          <input className="field" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          {t.password}
          <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && <div className="err">{error}</div>}
        <button className="btn lg" disabled={busy}>
          {t.signIn}
        </button>
        <LangSelect />
      </form>
    </div>
  )
}
