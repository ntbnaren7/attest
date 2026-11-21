import { useState } from 'react'
import { Loader2, Plus, X } from 'lucide-react'
import { api } from '../api'
import { errText } from '../errors'
import { usePrefs } from '../prefs'

export type Draft = { name: string; amount: string; phone: string; language: string; note: string; on: boolean }
export const blank = (): Draft => ({ name: '', amount: '', phone: '', language: '', note: '', on: true })

/** Editable rows of new customers; added through the normal importer (validation and fairness rules apply). */
export function NewCustomers({ initial, source, onAdded }: { initial: Draft[]; source: 'hand' | 'ledger'; onAdded: (refs: string[]) => void }) {
  const { t } = usePrefs()
  const [rows, setRows] = useState<Draft[]>(initial.length ? initial : [blank()])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<string[] | null>(null)
  const set = (i: number, k: keyof Draft, v: string | boolean) => setRows((r) => r.map((x, j) => (j === i ? { ...x, [k]: v } : x)))
  const chosen = rows.filter((r) => r.on && r.name.trim())

  async function add() {
    setBusy(true)
    setErr('')
    try {
      const rep = await api.addCustomers(chosen.map(({ on: _on, ...r }) => r), source)
      const problems = rep.invalid?.map((p) => `${p.row - 1}: ${p.errors.join('; ')}`) ?? []
      if (problems.length) setErr(`${t.needFixing}: ${problems.join(' · ')}`)
      setDone(rep.refs.slice(0, rep.valid_rows))
      onAdded(rep.refs)
    } catch (e) {
      setErr(errText(e, t))
    } finally {
      setBusy(false)
    }
  }

  if (done && !err) return <div className="success">✓ {done.length} {t.customersAdded}</div>
  return (
    <div className="newcust">
      <div className="nc-head small muted">
        <span />
        <span>{t.nameWord}</span>
        <span>{t.amountWord} (₹)</span>
        <span>{t.phoneWord}</span>
        <span />
      </div>
      {rows.map((r, i) => (
        <div key={i} className="nc-row">
          <input type="checkbox" checked={r.on} onChange={(e) => set(i, 'on', e.target.checked)} aria-label={r.name} />
          <input className="field" value={r.name} onChange={(e) => set(i, 'name', e.target.value)} placeholder={t.nameWord} />
          <input className="field num" value={r.amount} onChange={(e) => set(i, 'amount', e.target.value)} placeholder="5000" inputMode="decimal" />
          <input className="field num" value={r.phone} onChange={(e) => set(i, 'phone', e.target.value)} placeholder="9999999999" inputMode="tel" />
          <button type="button" className="icon-btn" onClick={() => setRows((x) => x.filter((_, j) => j !== i))} aria-label="remove">
            <X size={15} />
          </button>
          {r.note && <div className="nc-note small muted">“{r.note}”</div>}
        </div>
      ))}
      {err && <p className="err">{err}</p>}
      <div className="btn-row" style={{ marginTop: 10 }}>
        <button type="button" className="btn secondary" onClick={() => setRows((x) => [...x, blank()])}>
          <Plus size={15} /> {t.anotherRow}
        </button>
        <button type="button" className="btn" onClick={add} disabled={busy || chosen.length === 0}>
          {busy && <Loader2 size={15} className="spin" />} {t.addToBook} ({chosen.length})
        </button>
      </div>
    </div>
  )
}
