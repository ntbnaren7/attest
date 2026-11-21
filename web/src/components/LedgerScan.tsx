import { useRef, useState } from 'react'
import { Camera, CheckCircle2, Loader2 } from 'lucide-react'
import { api, type LedgerResult } from '../api'
import { errText } from '../errors'
import { shrink } from '../image'
import { usePrefs } from '../prefs'
import { useExit } from '../motion'
import { NewCustomers } from './NewCustomers'

/** Photograph a ledger / collection-diary page: OCR reads it, each line updates the matching account. */
export function LedgerScan({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t, lang } = usePrefs()
  const [closing, close] = useExit(onClose)
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<LedgerResult | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [err, setErr] = useState('')

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setErr('')
    setRes(null)
    try {
      const blob = await shrink(file)
      if (file.type !== 'application/pdf') setPreview(URL.createObjectURL(blob))
      setRes(await api.ledgerScan(blob, lang))
      onDone()
    } catch (e) {
      setErr(errText(e, t))
    } finally {
      setBusy(false)
    }
  }

  const matched = res?.lines.filter((l) => l.account) ?? []
  const proposals = res?.lines.filter((l) => !l.account && l.proposed) ?? []
  const other = res?.lines.filter((l) => !l.account && !l.proposed) ?? []

  return (
    <div className="dialog-wrap">
      <div className={`overlay ${closing ? 'closing' : ''}`} onClick={close} />
      <div className={`dialog wide ${closing ? 'closing' : ''}`} role="dialog" aria-modal="true" aria-labelledby="ledger-title">
        <h2 id="ledger-title">{t.scanLedger}</h2>
        <p className="intro">{t.scanLedgerHint}</p>
        {!res && (
          <div className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files[0]) }}>
            {busy ? <Loader2 size={24} className="spin" /> : <Camera size={24} strokeWidth={1.6} />}
            <div>{busy ? t.readingPage : 'JPG · PNG · PDF'}</div>
            <input ref={input} type="file" accept="image/*,application/pdf" capture="environment" className="sr" onChange={(e) => pick(e.target.files?.[0])} />
            <button className="btn" onClick={() => input.current?.click()} disabled={busy}>{t.chooseFile}</button>
          </div>
        )}
        {err && <p className="err">{err}</p>}
        {res && (
          <div className="ledger-res">
            {preview && <img src={preview} alt="" className="ledger-img" />}
            <div>
              <div className="success">
                <CheckCircle2 size={20} /> <span className="num">{res.matched}</span> {t.linesUpdated}
              </div>
              <ul className="ledger-lines">
                {matched.map((l, i) => (
                  <li key={i}>
                    <div className="who">{l.account!.display_name} <span className="muted">· {l.account!.external_ref}</span></div>
                    <div className="muted small">“{l.line}”</div>
                    <div className="small">{l.reply}</div>
                  </li>
                ))}
              </ul>
              {proposals.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div className="overline" style={{ marginBottom: 6 }}>{t.newOnPage}</div>
                  <NewCustomers
                    source="ledger"
                    onAdded={onDone}
                    initial={proposals.map((l) => ({
                      name: l.proposed!.name,
                      amount: l.proposed!.amount_paise != null ? String(l.proposed!.amount_paise / 100) : '',
                      phone: l.proposed!.phone,
                      language: '',
                      note: l.proposed!.note,
                      on: l.proposed!.amount_paise != null,
                    }))}
                  />
                </div>
              )}
              {other.length > 0 && (
                <details className="small muted">
                  <summary>{other.length} {t.linesNotLinked}</summary>
                  {other.map((l, i) => <div key={i}>“{l.line}”</div>)}
                </details>
              )}
            </div>
          </div>
        )}
        <div className="btn-row" style={{ marginTop: 18 }}>
          <button className="btn secondary" onClick={close}>{res ? t.doneWord : t.cancel}</button>
          {res && <button className="btn" onClick={() => { setRes(null); setPreview(null) }}>{t.scanAnother}</button>}
        </div>
      </div>
    </div>
  )
}
