import { LogOut, Moon, Sun } from 'lucide-react'
import type { Lang, Me } from '../api'
import { LANG_LABEL } from '../i18n'
import { usePrefs } from '../prefs'
import { Mark } from './Status'

export function LangSelect() {
  const { lang, setLang, t } = usePrefs()
  return (
    <select className="select" value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={t.language}>
      {(Object.keys(LANG_LABEL) as Lang[]).map((l) => (
        <option key={l} value={l}>
          {LANG_LABEL[l]}
        </option>
      ))}
    </select>
  )
}

export function TopBar({ me, onSwitch, onSignOut }: { me: Me; onSwitch: (persona: string) => void; onSignOut: () => void }) {
  const { t, theme, toggleTheme } = usePrefs()
  const ig = me.integrations
  const states = [
    { name: t.payments, s: ig.cashfree.mode === 'cashfree_sandbox' ? 'on' : 'practice' },
    { name: t.voice, s: ig.sarvam.mode === 'live' ? 'on' : 'off' },
    { name: t.messages, s: ig.relay.mode === 'inbound_webhook' ? 'on' : 'practice' },
  ]
  const word = (s: string) => (s === 'on' ? t.live : s === 'practice' ? t.fixture : t.off)
  const allOn = states.every((x) => x.s === 'on')
  const personas = Object.entries(me.personas ?? {})
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="wordmark">
          <Mark />
          <span className="name">Invariant</span>
        </div>

        {me.demo && personas.length > 1 ? (
          <div className="segmented" role="group" aria-label={t.scenario}>
            {personas.map(([id, p]) => (
              <button key={id} type="button" aria-pressed={me.operator.username === id} onClick={() => onSwitch(id)} title={p.detail}>
                {id === 'meena' ? t.loanBook : id === 'arun' ? t.invoices : p.label}
              </button>
            ))}
          </div>
        ) : (
          <span className="muted small">{me.operator.merchant_name}</span>
        )}

        <span className="spacer" />

        <span className="systems" title={states.map((x) => `${x.name}: ${word(x.s)}`).join(' · ')}>
          <span className="dots">
            {states.map((x) => (
              <i key={x.name} className={x.s === 'on' ? '' : x.s} />
            ))}
          </span>
          <span className="label">{allOn ? t.systemsOk : t.systemsPractice}</span>
        </span>
        <LangSelect />
        <button className="icon-btn" onClick={toggleTheme} aria-label={t.theme} title={t.theme}>
          {theme === 'light' ? <Moon size={17} strokeWidth={1.8} /> : <Sun size={17} strokeWidth={1.8} />}
        </button>
        {!me.demo && (
          <button className="icon-btn" onClick={onSignOut} aria-label={t.signOut} title={t.signOut}>
            <LogOut size={17} strokeWidth={1.8} />
          </button>
        )}
      </div>
    </header>
  )
}
