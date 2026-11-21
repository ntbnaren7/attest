import { useEffect, useState } from 'react'
import { ChevronDown, Loader2, Play } from 'lucide-react'
import { api, type ReconFinding, type ReconView as View } from '../api'
import { errText } from '../errors'
import { inr } from '../format'
import { transition } from '../motion'
import { usePrefs } from '../prefs'

// Engine amounts are rupees (2 decimals); our formatter takes paise.
const rs = (x: number) => inr(Math.round(x * 100))
const SEV: Record<ReconFinding['severity'], string> = { critical: 's-REVIEW', high: 's-HOLD', medium: 's-CLOSED', low: 's-CLOSED' }
const human = (k: string) => k.toLowerCase().replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** Books vs Cashfree vs bank, from the teammate's reconciliation engine (recon/). */
export function ReconView() {
  const { t } = usePrefs()
  const [v, setV] = useState<View | null>(null)
  const [mode, setMode] = useState<string>('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [pick, setPick] = useState<Record<string, boolean>>({})
  const [showTie, setShowTie] = useState(false)

  useEffect(() => {
    api.reconStatus().then((s) => setMode(s.available ? s.mode : 'unavailable')).catch(() => {})
  }, [])

  async function run() {
    setBusy('run')
    setErr('')
    try {
      const next = await api.reconRun()
      transition(() => {
        setV(next)
        setPick(Object.fromEntries(next.pending.map((p) => [p.id, true])))
      })
    } catch (e) {
      setErr(errText(e, t))
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    if (!v) return
    setBusy('approve')
    setErr('')
    try {
      setV(await api.reconApprove(v.thread_id, pick))
    } catch (e) {
      setErr(errText(e, t))
    } finally {
      setBusy(null)
    }
  }

  const decided = (v?.results.length ?? 0) > 0

  return (
    <>
      <div className="page-head">
        <div>
          <div className="overline">
            {mode === 'live' ? t.liveData : t.recorded}
            {v ? ` · ${v.run}` : ''}
          </div>
          <h1>{t.reconTitle}</h1>
          <p className="muted" style={{ margin: '6px 0 0' }}>{t.reconSub}</p>
        </div>
        <div className="head-actions">
          <button className="btn" onClick={run} disabled={!!busy || mode === 'unavailable'}>
            {busy === 'run' ? <Loader2 size={15} className="spin" /> : <Play size={15} />} {t.runRecon}
          </button>
        </div>
      </div>
      {err && <p className="err">{err}</p>}

      {!v ? (
        <div className="empty">{t.reconEmpty}</div>
      ) : (
        <>
          <section className="ledger">
            <div>
              <div className="overline">{t.atStake}</div>
              <div className="figure">{rs(v.kpi.at_risk)}</div>
              <div className="footnote">
                {v.kpi.critical} critical · {v.kpi.high} high
              </div>
            </div>
            <div>
              <div className="overline">{t.findingsWord}</div>
              <div className="figure">{v.kpi.findings}</div>
              <div className="footnote">{v.kpi.approvals} {t.needsApproval.toLowerCase()}</div>
            </div>
            <div>
              <div className="overline">Tie-out</div>
              <div className={`figure ${v.tieout.ok ? 'good' : ''}`} style={v.tieout.ok ? undefined : { color: 'var(--check)' }}>
                {v.tieout.ok ? '✓' : '✗'}
              </div>
              <div className="footnote">{v.tieout.ok ? t.tieoutOk : t.tieoutBreak}</div>
            </div>
          </section>

          <div className="main">
            <section>
              <div className="tab-note" style={{ borderTop: '1px solid var(--rule)' }}>
                <span>
                  <span className="sum">{rs(v.kpi.at_risk)}</span> {t.across} {v.kpi.findings} {t.findingsWord.toLowerCase()}
                </span>
              </div>
              <ul className="rows">
                {v.findings.map((f, i) => (
                  <li key={`${f.agent}-${f.entity}-${f.kind}-${i}`}>
                    <div className={`row ${SEV[f.severity]}`} style={{ cursor: 'default' }}>
                      <span className="dot" />
                      <span style={{ minWidth: 0 }}>
                        <span className="who" style={{ display: 'block' }}>{human(f.kind)}</span>
                        <span className="ref" style={{ display: 'block' }}>
                          {f.agent} · {f.severity}
                        </span>
                      </span>
                      <span className="reason">{f.summary}</span>
                      <span>
                        <span className="amount" style={{ display: 'block' }}>{rs(f.amount)}</span>
                        {f.deadline?.due && (
                          <span className={`when ${f.deadline.overdue ? 'warn' : ''}`} style={{ display: 'block' }}>
                            {t.due} {f.deadline.due.slice(0, 10)}
                          </span>
                        )}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <aside className="side">
              <section className="panel">
                <h2>{t.needsApproval}</h2>
                <p className="lede">{t.dryRunNote}</p>
                <ul className="facts">
                  {v.pending.map((p) => {
                    const res = v.results.find((r) => r.id === p.id)
                    return (
                      <li key={p.id}>
                        <input
                          type="checkbox"
                          checked={!!pick[p.id]}
                          disabled={decided}
                          onChange={(e) => setPick((x) => ({ ...x, [p.id]: e.target.checked }))}
                          aria-label={p.summary}
                          style={{ accentColor: 'var(--accent)', marginTop: 3 }}
                        />
                        <span>
                          {p.summary}
                          <div className="sub">
                            {p.plan.api ? `${t.wouldSend}: ${String(p.plan.api)}` : t.advisory}
                            {res ? ` · ${res.status.replace(/_/g, ' ').toLowerCase()}` : ''}
                          </div>
                        </span>
                        <span className="amt">{rs(p.amount)}</span>
                      </li>
                    )
                  })}
                </ul>
                <div className="btn-row" style={{ marginTop: 14 }}>
                  <button className="btn" onClick={approve} disabled={!!busy || decided || v.pending.length === 0}>
                    {busy === 'approve' && <Loader2 size={15} className="spin" />} {t.approveDry}
                  </button>
                </div>
              </section>

              <section className="panel">
                <button className="btn text" style={{ paddingLeft: 0 }} onClick={() => setShowTie((x) => !x)} aria-expanded={showTie}>
                  <ChevronDown size={15} style={{ transform: showTie ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} /> {t.showTieout}
                </button>
                {showTie &&
                  v.tieout.sections.map((s) => (
                    <div key={s.title} style={{ marginTop: 14 }}>
                      <div className="overline" style={{ marginBottom: 4 }}>{s.title}</div>
                      <ul className="timeline">
                        {s.lines.map((l, j) => (
                          <li key={j} style={{ gridTemplateColumns: '1fr auto' }}>
                            <span>{l.label}</span>
                            <span className="num">{rs(l.amount)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
              </section>
            </aside>
          </div>
        </>
      )}
    </>
  )
}
