import { useState } from 'react'
import { CheckCircle2, Loader2, ShieldCheck } from 'lucide-react'
import { api, ApiError, type Approval } from '../api'
import { inr } from '../format'
import { usePrefs } from '../prefs'
import { HelpButton } from './HelpButton'

/** The only place money-moving actions are authorized. Voice never reaches this. */
export function ApproveDialog({ approval, name, reason, onClose, onDone }: {
  approval: Approval
  name: string
  reason: string
  onClose: () => void
  onDone: () => void
}) {
  const { t } = usePrefs()
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<{ title: string; detail: string } | null>(null)
  const [done, setDone] = useState(false)

  async function go() {
    setBusy(true)
    setErr(null)
    try {
      const { token } = await api.approve(approval)
      await api.execute(approval.id, token)
      setDone(true)
      onDone()
    } catch (e) {
      const ae = e instanceof ApiError ? e : null
      setErr({ title: ae?.code === 'STALE_APPROVAL' ? t.staleTitle : ae?.code ?? 'Error', detail: ae?.message ?? String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dialog-wrap">
      <div className="scrim" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="approve-title" style={{ zIndex: 31 }}>
        {done ? (
          <>
            <div className="notice s-CONTACT" style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 16 }}>
              <CheckCircle2 /> {t.sent}
            </div>
            <div className="actions">
              <button className="btn" onClick={onClose}>
                {t.close}
              </button>
            </div>
          </>
        ) : (
          <>
            <div>
              <h2 id="approve-title">{t.approveTitle}</h2>
              <p className="muted" style={{ margin: '6px 0 0' }}>{t.approveIntro}</p>
            </div>
            <dl className="kv">
              <dt>{t.to}</dt>
              <dd>
                {name} <span className="muted" style={{ fontWeight: 400 }}>· {approval.account_id.slice(-6)}</span>
              </dd>
              <dt>{t.phone}</dt>
              <dd className="num">{approval.recipient}</dd>
              <dt>{t.amount}</dt>
              <dd className="amount num">{inr(approval.amount_paise)}</dd>
            </dl>
            <div style={{ display: 'flex', gap: 10 }}>
              <ShieldCheck size={20} style={{ color: 'var(--call)', flex: 'none', marginTop: 2 }} />
              <div>
                <div className="small muted">{t.safeBecause}</div>
                <div>{reason}</div>
              </div>
            </div>
            <label className="check">
              <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
              <span>{t.iChecked}</span>
            </label>
            {err && (
              <div className="notice s-REVIEW">
                <b>{err.title}</b>
                <div className="small">{err.detail}</div>
              </div>
            )}
            <div className="actions" style={{ alignItems: 'center' }}>
              <HelpButton topic="approve" label={t.approveSend} />
              <button className="btn ghost" onClick={onClose}>
                {t.cancel}
              </button>
              <button className="btn big" onClick={go} disabled={!checked || busy}>
                {busy && <Loader2 size={18} className="spin" />} {t.approveSend}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
