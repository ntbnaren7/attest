import { Check, LogOut, Moon, Sun } from 'lucide-react'
import type { Lang, Me } from '../api'
import { LANG_LABEL } from '../i18n'
import { usePrefs } from '../prefs'

export function LangSwitch() {
  const { lang, setLang, t } = usePrefs()
  return (
    <div className="seg" role="group" aria-label={t.language}>
      {(Object.keys(LANG_LABEL) as Lang[]).map((l) => (
        <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}>
          {LANG_LABEL[l]}
        </button>
      ))}
    </div>
  )
}

function Conn({ label, status, state }: { label: string; status: string; state: 'on' | 'practice' | 'off' }) {
  return (
    <span title={`${label}: ${status}`}>
      <i className={state === 'on' ? 'on' : state === 'practice' ? 'practice' : ''} />
      {label}
      <span className="sr">: {status}</span>
    </span>
  )
}

export function TopBar({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const { t, theme, toggleTheme } = usePrefs()
  const ig = me.integrations
  const pay = ig.cashfree.mode === 'cashfree_sandbox' ? 'on' : 'practice'
  const voice = ig.sarvam.mode === 'live' ? 'on' : 'off'
  const msgs = ig.relay.mode === 'inbound_webhook' ? 'on' : 'practice'
  const word = (s: string) => (s === 'on' ? t.live : s === 'practice' ? t.fixture : t.off)
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          <div className="brand-mark">
            <Check size={20} strokeWidth={3} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="brand-name">{t.appName}</div>
            <div className="brand-sub">
              {me.operator.merchant_name} · {me.operator.mode === 'borrower' ? t.loanBook : t.invoices}
            </div>
          </div>
        </div>
        <div className="conn" aria-label="Connections">
          <Conn label={t.payments} status={word(pay)} state={pay} />
          <Conn label={t.voice} status={word(voice)} state={voice} />
          <Conn label={t.messages} status={word(msgs)} state={msgs} />
        </div>
        <LangSwitch />
        <button className="icon-btn" onClick={toggleTheme} aria-label={t.theme} title={t.theme}>
          {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
        </button>
        <button className="btn quiet" onClick={onSignOut}>
          <LogOut size={16} /> <span className="small">{me.operator.display_name.split(' (')[0]}</span>
        </button>
      </div>
    </header>
  )
}
