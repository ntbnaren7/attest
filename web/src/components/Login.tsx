import { useState, type FormEvent } from 'react'
import { Check } from 'lucide-react'
import { api } from '../api'
import { usePrefs } from '../prefs'
import { LangSwitch } from './TopBar'

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
      <form className="card" onSubmit={submit}>
        <div className="brand" style={{ marginRight: 0 }}>
          <div className="brand-mark">
            <Check size={20} strokeWidth={3} />
          </div>
          <div>
            <div className="brand-name">{t.appName}</div>
            <div className="brand-sub">{t.signInHint}</div>
          </div>
        </div>
        <LangSwitch />
        <label>
          {t.username}
          <input className="field" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          {t.password}
          <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && <div className="err">{error}</div>}
        <button className="btn big" disabled={busy}>
          {t.signIn}
        </button>
      </form>
    </div>
  )
}
