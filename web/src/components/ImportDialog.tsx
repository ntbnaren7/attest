import { useRef, useState } from 'react'
import { CheckCircle2, FileUp, Loader2 } from 'lucide-react'
import { api, type ImportReport } from '../api'
import { errText } from '../errors'
import { usePrefs } from '../prefs'

export function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = usePrefs()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [rep, setRep] = useState<ImportReport | null>(null)
  const [err, setErr] = useState('')

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setErr('')
    setRep(null)
    try {
      setRep(await api.importFile(file))
      onDone()
    } catch (e) {
      setErr(errText(e, t))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dialog-wrap">
      <div className="overlay" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="import-title">
        <h2 id="import-title">{t.addList}</h2>
        <p className="intro">{t.addListHint}</p>
        {!rep && (
          <div
            className="drop"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              pick(e.dataTransfer.files[0])
            }}
          >
            {busy ? <Loader2 size={24} className="spin" /> : <FileUp size={24} strokeWidth={1.6} />}
            <div>{busy ? t.uploading : 'CSV · JSON'}</div>
            <input ref={input} type="file" accept=".csv,.json" className="sr" onChange={(e) => pick(e.target.files?.[0])} />
            <button className="btn" onClick={() => input.current?.click()} disabled={busy}>
              {t.chooseFile}
            </button>
          </div>
        )}
        {err && <p className="err">{err}</p>}
        {rep && (
          <div className="report">
            <div className="success">
              <CheckCircle2 size={20} /> <span className="num">{rep.valid_rows}</span> {t.added}
            </div>
            {rep.invalid_rows > 0 && (
              <div>
                <div className="overline" style={{ color: 'var(--check)', marginBottom: 6 }}>
                  {rep.invalid_rows} {t.needFixing}
                </div>
                <div className="problems">
                  {rep.invalid.map((p) => (
                    <div key={p.row}>
                      <b>
                        {t.row} {p.row}
                      </b>
                      {p.account_ref ? ` · ${p.account_ref}` : ''}: <span className="muted">{p.errors.join('; ')}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {rep.protected_columns_dropped.length > 0 && (
              <div>
                <span className="muted">{t.hiddenForFairness}:</span> {rep.protected_columns_dropped.join(', ')}
              </div>
            )}
            {rep.amount_changes.length > 0 && (
              <div>
                <span className="muted">{t.changedAmounts}:</span> {rep.amount_changes.map((c) => `${c.invoice} ${c.from} → ${c.to}`).join('; ')}
              </div>
            )}
          </div>
        )}
        <div className="footer">
          <button className="btn secondary" onClick={onClose}>
            {t.close}
          </button>
        </div>
      </div>
    </div>
  )
}
