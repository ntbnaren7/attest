import { useRef, useState } from 'react'
import { CheckCircle2, FileUp, Loader2, TriangleAlert } from 'lucide-react'
import { api, ApiError, type ImportReport } from '../api'
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
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dialog-wrap">
      <div className="scrim" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="import-title" style={{ zIndex: 31 }}>
        <h2 id="import-title">{t.addList}</h2>
        {!rep && (
          <div
            className="drop"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              pick(e.dataTransfer.files[0])
            }}
          >
            {busy ? <Loader2 size={28} className="spin" /> : <FileUp size={28} />}
            <div className="muted">{busy ? t.uploading : t.addListHint}</div>
            <input ref={input} type="file" accept=".csv,.json" className="sr" onChange={(e) => pick(e.target.files?.[0])} />
            <button className="btn" onClick={() => input.current?.click()} disabled={busy}>
              {t.chooseFile}
            </button>
          </div>
        )}
        {err && <div className="notice s-REVIEW">{err}</div>}
        {rep && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div className="notice s-CONTACT" style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 16 }}>
              <CheckCircle2 /> <b className="num">{rep.valid_rows}</b> {t.added}
            </div>
            {rep.invalid_rows > 0 && (
              <div className="notice s-REVIEW">
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 600 }}>
                  <TriangleAlert size={18} /> <span className="num">{rep.invalid_rows}</span> {t.needFixing}
                </div>
                <div className="problems" style={{ color: 'var(--ink)', marginTop: 8 }}>
                  {rep.invalid.map((p) => (
                    <div key={p.row}>
                      <b>
                        {t.row} {p.row}
                      </b>
                      {p.account_ref ? ` · ${p.account_ref}` : ''} — {p.errors.join('; ')}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {rep.protected_columns_dropped.length > 0 && (
              <div className="small">
                <span className="muted">{t.hiddenForFairness}:</span> {rep.protected_columns_dropped.join(', ')}
              </div>
            )}
            {rep.amount_changes.length > 0 && (
              <div className="small">
                <span className="muted">{t.changedAmounts}:</span> {rep.amount_changes.map((c) => `${c.invoice} ${c.from} → ${c.to}`).join('; ')}
              </div>
            )}
          </div>
        )}
        <div className="actions">
          <button className="btn ghost" onClick={onClose}>
            {t.close}
          </button>
        </div>
      </div>
    </div>
  )
}
