import { useEffect, useRef, useState } from 'react'
import { Loader2, Play } from 'lucide-react'
import { api, type OpsEvent, type OpsSummary } from '../api'
import { errText } from '../errors'
import { inr, time } from '../format'
import { usePrefs } from '../prefs'

/** Live operations: agents at work, the 24/7 daemon, recovered revenue vs leakage. Fed by server-sent events. */
export function OpsView({ events, live }: { events: OpsEvent[]; live: boolean }) {
  const { t } = usePrefs()
  const [s, setS] = useState<OpsSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const timer = useRef<number | undefined>(undefined)

  const load = () => api.ops().then(setS).catch((e) => setErr(errText(e, t)))
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    if (!events.length) return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(load, 600)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events])

  async function runNow() {
    setBusy(true)
    setErr('')
    try {
      await api.daemonRun()
      await load()
    } catch (e) {
      setErr(errText(e, t))
    } finally {
      setBusy(false)
    }
  }

  // stream first (newest), then whatever the summary had before the page opened
  const seen = new Set(events.map((e) => e.id))
  const feed = [...events, ...[...(s?.activity ?? [])].reverse().filter((e) => !seen.has(e.id))].slice(0, 60)
  const d = s?.daemon

  return (
    <>
      <div className="page-head">
        <div>
          <div className="overline">
            <span className={`live-dot ${live ? 'on' : ''}`} /> {live ? t.liveConnected : t.liveReconnecting}
          </div>
          <h1>{t.opsTitle}</h1>
          <p className="muted" style={{ margin: '6px 0 0' }}>{t.opsSub}</p>
        </div>
        <div className="head-actions">
          <button className="btn" onClick={runNow} disabled={busy}>
            {busy ? <Loader2 size={15} className="spin" /> : <Play size={15} />} {t.runDaemon}
          </button>
        </div>
      </div>
      {err && <p className="err">{err}</p>}

      <section className="ledger four">
        <div>
          <div className="overline">{t.recoveredWord}</div>
          <div className="figure good">{s ? inr(s.recovered_paise) : '–'}</div>
          <div className="footnote">{t.verifiedByCashfree}</div>
        </div>
        <div>
          <div className="overline">{t.leakageWord}</div>
          <div className="figure" style={{ color: s?.leakage_paise ? 'var(--check)' : undefined }}>
            {s?.leakage_paise != null ? inr(s.leakage_paise) : '–'}
          </div>
          <div className="footnote">{s?.leakage_findings != null ? `${s.leakage_findings} ${t.findingsWord.toLowerCase()}` : t.runReconFirst}</div>
        </div>
        <div>
          <div className="overline">{t.agentSuccess}</div>
          <div className="figure">{s?.success_rate != null ? `${Math.round(s.success_rate * 100)}%` : '–'}</div>
          <div className="footnote">
            {s?.runs_24h ?? 0} {t.stepsToday}
          </div>
        </div>
        <div>
          <div className="overline">{t.daemonWord}</div>
          <div className="figure">{s?.daemon_running ? t.daemonOn : t.daemonOff}</div>
          <div className="footnote">
            {d ? `${t.lastPass} ${time(d.at)} · ${d.eligible}/${d.ranked}` : `${t.every} ${s?.daemon_interval_seconds ?? 60}s`}
            {d?.next ? ` · ${t.nextCustomer}: ${d.next.name}` : ''}
          </div>
        </div>
      </section>

      <div className="main">
        <section>
          <div className="tab-note" style={{ borderTop: '1px solid var(--rule)' }}>
            <span>{t.liveActivity}</span>
          </div>
          {feed.length === 0 ? (
            <div className="empty">{t.noActivity}</div>
          ) : (
            <ul className="ops-feed">
              {feed.map((e) => (
                <li key={e.id} className={e.ok ? '' : 'bad'}>
                  <span className="when num">{time(e.ts)}</span>
                  <span className="agent">{e.agent}</span>
                  <span className="what">
                    {e.ref && <b>{e.ref} · </b>}
                    {e.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className="side">
          <section className="panel">
            <h2>{t.agentsWord}</h2>
            <ul className="timeline">
              {(s?.agents ?? []).map((a) => (
                <li key={a.agent} style={{ gridTemplateColumns: '1fr auto' }}>
                  <span>{a.agent}</span>
                  <span className="num small">
                    {a.ok}/{a.runs}
                    {a.errors > 0 && <span style={{ color: 'var(--check)' }}> · {a.errors} ✗</span>}
                  </span>
                </li>
              ))}
            </ul>
          </section>
          {d && (d.prepared.length > 0 || d.waiting_for_contact_hours.length > 0) && (
            <section className="panel">
              <h2>{t.daemonWord}</h2>
              {d.prepared.length > 0 && (
                <p className="lede">
                  {t.preparedForApproval}: {d.prepared.join(', ')}
                </p>
              )}
              {d.waiting_for_contact_hours.length > 0 && (
                <p className="lede">
                  {t.waitingHours}: {d.waiting_for_contact_hours.join(', ')}
                </p>
              )}
            </section>
          )}
        </aside>
      </div>
    </>
  )
}
