import { useState } from 'react'
import { CheckCircle2, Loader2, ShieldCheck } from 'lucide-react'
import { api, ApiError, type Approval } from '../api'
import { errText } from '../errors'
import { inr } from '../format'
import { usePrefs } from '../prefs'
import { useExit } from '../motion'
import { HelpButton } from './HelpButton'

/** The only place a money-moving action is authorized. Voice never reaches this. */
export function ApproveDialog({ approval, name, reason, onClose, onDone }: {
  approval: Approval
  name: string
  reason: string
  onClose: () => void
  onDone: () => void
}) {
  const { t } = usePrefs()
  const [closing, close] = useExit(onClose)
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
      setErr({ title: ae?.code === 'STALE_APPROVAL' ? t.staleTitle : errText(e, t), detail: ae?.code === 'STALE_APPROVAL' ? ae.message : '' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dialog-wrap">
      <div className={`overlay ${closing ? 'closing' : ''}`} onClick={close} />
      <div className={`dialog ${closing ? 'closing' : ''}`} role="dialog" aria-modal="true" aria-labelledby="approve-title">
        {done ? (
          <>
            <div className="success">
              <CheckCircle2 size={22} /> {t.sent}
            </div>
            <div className="footer">
              <button className="btn" onClick={close}>
                {t.close}
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 id="approve-title">{t.approveTitle}</h2>
            <p className="intro">{t.approveIntro}</p>
            <dl className="terms">
              <div>
                <dt>{t.to}</dt>
                <dd>{name}</dd>
              </div>
              <div>
                <dt>{t.phone}</dt>
                <dd>{approval.recipient}</dd>
              </div>
              <div>
                <dt>{t.amount}</dt>
                <dd className="big">{inr(approval.amount_paise)}</dd>
              </div>
            </dl>
            <div className="why-safe">
              <ShieldCheck size={18} />
              <div>
                <div className="overline" style={{ marginBottom: 2 }}>{t.safeBecause}</div>
                {reason}
              </div>
            </div>
            <label className="check">
              <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
              <span>{t.iChecked}</span>
            </label>
            {err && (
              <div className="callout s-REVIEW" style={{ marginTop: 16 }}>
                <b>{err.title}</b>
                {err.detail && <div className="small">{err.detail}</div>}
              </div>
            )}
            <div className="footer">
              <HelpButton topic="approve" label={t.approveSend} />
              <span className="spacer" />
              <button className="btn secondary" onClick={close}>
                {t.cancel}
              </button>
              <button className="btn" onClick={go} disabled={!checked || busy}>
                {busy && <Loader2 size={16} className="spin" />} {t.approveSend}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
