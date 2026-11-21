import { useCallback, useEffect, useState } from 'react'
import { Check, CircleDashed, Copy, ExternalLink, Loader2, Play, RefreshCw, X } from 'lucide-react'
import { api, type Approval, type CheckResult, type LiveCheck, type LiveStatus, type Queue } from '../api'
import { errText } from '../errors'
import { ago, inr, time } from '../format'
import { useExit } from '../motion'
import { usePrefs } from '../prefs'

/**
 * Everything needed to run the live demo from the dashboard: connection checks, a guided
 * pay-and-watch scenario, and a live view of webhooks, polls and receipts. Operator tooling,
 * so it is written in English.
 */
export function LivePanel({ q, onClose, onApprove, onChanged, onOpen }: {
  q: Queue | null
  onClose: () => void
  onApprove: (a: Approval, name: string, reason: string) => void
  onChanged: () => void
  onOpen: (id: string) => void
}) {
  const { t } = usePrefs()
  const [closing, close] = useExit(onClose)
  const [s, setS] = useState<LiveStatus | null>(null)
  const [check, setCheck] = useState<LiveCheck | null>(null)
  const [withRelay, setWithRelay] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => api.liveStatus().then(setS).catch(() => {}), [])
  useEffect(() => {
    load()
    const timer = window.setInterval(load, 2000)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && close()
    window.addEventListener('keydown', esc)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('keydown', esc)
    }
  }, [load, close])

  async function run(name: string, fn: () => Promise<unknown>) {
    setBusy(name)
    setMsg('')
    try {
      await fn()
      await load()
      onChanged()
    } catch (e) {
      setMsg(errText(e, t))
    } finally {
      setBusy(null)
    }
  }

  const live = s?.cashfree.mode === 'cashfree_sandbox'
  const top = q?.rows.find((r) => r.decision === 'CONTACT' && r.next_actions.includes('request_payment_link'))
  const link = s?.links[0]
  const paidSome = !!link && link.amount_paid_paise > 0
  const fullyPaid = !!link && link.status === 'PAID'
  const hasList = (q?.rows.length ?? 0) > 0
  const steps = [hasList, !!link, paidSome, fullyPaid]
  const via = (a: string | null) => (a === 'cashfree_webhook' ? 'Cashfree webhook' : a === 'reconciler' ? 'provider poll' : a ? 'manual re-check' : '')

  return (
    <>
      <div className={`overlay ${closing ? 'closing' : ''}`} onClick={close} />
      <aside className={`drawer live ${closing ? 'closing' : ''}`} role="dialog" aria-modal="true" aria-label="Live test">
        <div className="drawer-top">
          <span className="status s-CONTACT">
            <span className="dot pulse-dot" /> Watching live
          </span>
          <button className="icon-btn" onClick={close} aria-label={t.close}>
            <X size={18} strokeWidth={1.8} />
          </button>
        </div>
        <div className="drawer-body">
          <h2>Live test</h2>
          <div className="meta">Run the real thing from here: Cashfree sandbox, Sarvam and Relay. No terminal needed.</div>
          {msg && <p className="err">{msg}</p>}

          {/* ------------------------------------------------------------ scenario */}
          <div className="block">
            <div className="overline">Live scenario</div>
            <ol className="steps-live">
              <Step n={1} done={steps[0]} title="Load a list">
                <div className="btn-row">
                  {s &&
                    Object.entries(s.samples).map(([k, label]) => (
                      <button key={k} className="btn secondary sm" disabled={!!busy} onClick={() => run(k, () => api.liveSample(k))} title={label}>
                        {busy === k && <Loader2 size={14} className="spin" />}
                        {label.split(' (')[0]}
                      </button>
                    ))}
                </div>
              </Step>
              <Step n={2} done={steps[1]} title="Prepare a payment link and approve it">
                {top ? (
                  <button
                    className="btn sm"
                    disabled={!!busy}
                    onClick={() =>
                      run('link', async () => {
                        const res = await api.requestLink(top.account_id)
                        if (res.facts.approval) onApprove(res.facts.approval, top.display_name || top.external_ref, top.reason)
                        else throw new Error(res.summary)
                      })
                    }
                  >
                    {busy === 'link' ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
                    {top.display_name || top.external_ref} · {inr(top.collectible_paise)}
                  </button>
                ) : (
                  <span className="muted small">{hasList ? 'Nobody is in “Call now” right now.' : 'Load a list first.'}</span>
                )}
              </Step>
              <Step n={3} done={steps[2]} title={live ? 'Pay it on the Cashfree test page' : 'Pay it (practice data)'}>
                {link && link.status !== 'PAID' ? (
                  live ? (
                    <div className="btn-row">
                      {link.link_url && (
                        <a className="btn sm" href={link.link_url} target="_blank" rel="noopener noreferrer">
                          <ExternalLink size={14} /> Open payment page
                        </a>
                      )}
                      <button
                        className="btn secondary sm"
                        onClick={() => {
                          navigator.clipboard?.writeText(s?.test_upi ?? '').catch(() => {})
                          setCopied(true)
                          window.setTimeout(() => setCopied(false), 1500)
                        }}
                      >
                        {copied ? <Check size={14} /> : <Copy size={14} />} UPI {s?.test_upi}
                      </button>
                    </div>
                  ) : (
                    <div className="btn-row">
                      <button className="btn secondary sm" onClick={() => run('sim', () => api.simulatePay(link.link_id, String(Math.floor((link.amount_paise - link.amount_paid_paise) / 200))))}>
                        Pay part
                      </button>
                      <button className="btn sm" onClick={() => run('sim', () => api.simulatePay(link.link_id))}>
                        Pay all
                      </button>
                    </div>
                  )
                ) : (
                  <span className="muted small">{link ? 'Paid.' : 'Waiting for a link.'}</span>
                )}
              </Step>
              <Step n={4} done={steps[3]} title="Watch it land">
                {link ? (
                  <div className="small">
                    <button className="link-btn" onClick={() => onOpen(link.account_id)}>
                      {link.display_name || link.external_ref}
                    </button>{' '}
                    · {linkWord(link.status)} · <span className="num">{inr(link.amount_paid_paise)} / {inr(link.amount_paise)}</span>
                    {link.caught_by && <div className="muted">First seen by: {via(link.caught_by)}</div>}
                  </div>
                ) : (
                  <span className="muted small">The queue updates by itself when money arrives.</span>
                )}
              </Step>
            </ol>
          </div>

          {/* ------------------------------------------------------------ watcher */}
          <div className="block">
            <div className="overline">Payment watcher</div>
            <div className="watch-head">
              <span>
                Polls Cashfree every <b>{s?.watcher.poll_every_s ?? '…'} s</b>
                {s?.watcher.last_poll_at && <span className="muted"> · last {ago(s.watcher.last_poll_at, t)}</span>}
              </span>
              <button className="btn text sm" disabled={!!busy} onClick={() => run('poll', () => api.livePoll())}>
                {busy === 'poll' ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} Check now
              </button>
            </div>
            <div className="small muted" style={{ marginBottom: 8 }}>
              Webhooks:{' '}
              {s?.webhooks.public_url ? (
                <>
                  <code>{s.webhooks.public_url}</code> · {s.webhooks.received} accepted · {s.webhooks.rejected} rejected
                </>
              ) : (
                'no public URL set, so payments are picked up by polling'
              )}
            </div>
            <ul className="facts">
              {(s?.links ?? []).slice(0, 4).map((l) => (
                <li key={l.link_id}>
                  <span className={`mk ${l.status === 'PAID' ? 'ok' : l.amount_paid_paise ? 'said' : 'bill'}`} />
                  <span>
                    {l.display_name || l.external_ref} · {linkWord(l.status)}
                    <div className="sub">
                      {l.provenance === 'fixture' ? 'Practice data' : 'Cashfree sandbox'} · created {time(l.created_at)}
                      {l.caught_by ? ` · caught by ${via(l.caught_by)}` : ''}
                    </div>
                  </span>
                  <span className="amt">
                    {inr(l.amount_paid_paise)} / {inr(l.amount_paise)}
                  </span>
                </li>
              ))}
              {(s?.webhook_events ?? []).slice(0, 4).map((w, i) => (
                <li key={`w${i}`}>
                  <span className={`mk ${w.signature_ok ? 'ok' : 'said'}`} />
                  <span>
                    Webhook {w.event_type ?? ''} · {w.signature_ok ? 'signature checked' : 'rejected'}
                    <div className="sub">{(w.outcome ?? '').slice(0, 110)}</div>
                  </span>
                  <span className="sub num">{time(w.received_at)}</span>
                </li>
              ))}
              {s && s.links.length === 0 && s.webhook_events.length === 0 && <li className="muted small">Nothing yet.</li>}
            </ul>
          </div>

          {/* ------------------------------------------------------------ connections */}
          <div className="block">
            <div className="overline">Connections</div>
            <ul className="facts">
              <Conn name="Cashfree" state={live ? 'Sandbox, live API' : 'Practice data'} result={check?.cashfree} />
              <Conn
                name="Sarvam"
                state={s?.sarvam.live ? `${s.sarvam.models.join(' · ')} · ${s.sarvam.calls_today}/${s.sarvam.daily_budget} calls today` : 'Not configured'}
                result={check?.sarvam}
              />
              <Conn name="Relay" state={s?.relay.mode === 'inbound_webhook' ? 'Inbound webhook agent' : 'Manual hand-off'} result={check?.relay} />
            </ul>
            <label className="check small" style={{ marginTop: 12 }}>
              <input type="checkbox" checked={withRelay} onChange={(e) => setWithRelay(e.target.checked)} />
              <span>Also send a Relay test email</span>
            </label>
            <div className="btn-row" style={{ marginTop: 10 }}>
              <button className="btn" disabled={!!busy} onClick={() => run('check', async () => setCheck(await api.liveCheck(withRelay)))}>
                {busy === 'check' ? <Loader2 size={15} className="spin" /> : <Play size={15} />} Run live check
              </button>
              <span className="muted small">Makes real test calls: a ₹1 Cashfree link, one Sarvam call and voice.</span>
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}

function linkWord(s: string) {
  return { ACTIVE: 'sent, not paid', PARTIALLY_PAID: 'part paid', PAID: 'paid', EXPIRED: 'expired', CANCELLED: 'cancelled' }[s] ?? s
}

function Step({ n, done, title, children }: { n: number; done: boolean; title: string; children: React.ReactNode }) {
  return (
    <li className={done ? 'done' : ''}>
      <span className="step-mark" aria-hidden="true">
        {done ? <Check size={13} strokeWidth={2.6} /> : n}
      </span>
      <div>
        <div className="step-title">{title}</div>
        <div className="step-body">{children}</div>
      </div>
    </li>
  )
}

function Conn({ name, state, result }: { name: string; state: string; result?: CheckResult }) {
  return (
    <li>
      <span className={`mk ${result?.ok ? 'ok' : result?.ok === false ? 'said' : 'bill'}`} />
      <span>
        {name}
        <div className="sub">{state}</div>
        {result && (
          <div className="sub" style={{ color: result.ok ? 'var(--call)' : result.ok === false ? 'var(--check)' : 'var(--muted)' }}>
            {result.ok ? 'Passed' : result.ok === false ? 'Failed' : 'Not run'}: {result.detail}
          </div>
        )}
      </span>
      <span className="sub num">{result?.ms ? `${result.ms} ms` : result ? '' : <CircleDashed size={13} />}</span>
    </li>
  )
}
