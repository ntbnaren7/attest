import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, MessageSquareQuote, RefreshCw, Send, Volume2, X } from 'lucide-react'
import { api, ApiError, type Approval, type CaseDetail, type TraceRow } from '../api'
import { ago, inr, time } from '../format'
import { type Key } from '../i18n'
import { usePrefs } from '../prefs'
import { StatusIcon, StatusPill } from './Status'

const CLAIM_WORDS: Record<string, string> = {
  paid: 'says it is already paid',
  promise: 'promised to pay',
  deduction: 'wants a deduction',
  dispute: 'disputes the bill',
  hardship: 'cannot pay right now',
  opt_out: 'asked not to be called',
}

const ACTION_WORDS: Record<string, string> = {
  check_payment_reference: 'Checked the payment with Cashfree',
  refresh_account: 'Re-checked payments',
  refresh_link: 'Checked the payment link',
  record_claim: 'Noted what was said',
  allocate_receipts: 'Matched money to bills',
  evaluate_case: 'Decided',
  propose_payment_link: 'Prepared a link for approval',
  hold_outreach: 'Paused reminders',
  deliver_link: 'Sent the link',
}

function describe(tr: TraceRow): { text: string; bad?: boolean } | null {
  const r = (tr.result ?? {}) as Record<string, unknown>
  switch (tr.kind) {
    case 'handoff':
      if (tr.allowed_action === 'evaluate_case' || tr.allowed_action === 'hold_outreach') return null
      return { text: `${ACTION_WORDS[tr.allowed_action ?? ''] ?? tr.allowed_action}: ${r.summary ?? ''}` }
    case 'denial':
      return { text: `Blocked: ${r.reason ?? ''}`, bad: true }
    case 'approval_requested':
      return { text: `Link for ${inr(Number(r.amount_paise ?? 0))} waiting for approval` }
    case 'approval_granted':
      return { text: `Approved by ${tr.actor.replace('operator:', '')}` }
    case 'action_executed':
      return { text: `Payment link created (${inr(Number(r.amount_paise ?? 0))})` }
    case 'queue_change':
      return { text: `Moved to ${String(r.to)}: ${r.why ?? ''}` }
    case 'webhook_rejected':
      return { text: `Ignored a message that was not really from Cashfree`, bad: true }
    default:
      return null
  }
}

export function CaseDrawer({ id, onClose, onChanged, onApprove }: {
  id: string
  onClose: () => void
  onChanged: () => void
  onApprove: (a: Approval, name: string, reason: string) => void
}) {
  const { t, lang } = usePrefs()
  const [c, setC] = useState<CaseDetail | null>(null)
  const [reasonLocal, setReasonLocal] = useState<string>('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const audio = useRef<HTMLAudioElement | null>(null)

  const load = useCallback(async () => {
    const [detail, ex] = await Promise.all([api.caseDetail(id), lang === 'en-IN' ? Promise.resolve(null) : api.explain(id, lang).catch(() => null)])
    setC(detail)
    setReasonLocal(ex?.text ?? detail.decision.reason)
  }, [id, lang])

  useEffect(() => {
    load().catch((e) => setErr(String(e.message ?? e)))
    const timer = window.setInterval(() => load().catch(() => {}), 4000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function hear() {
    setBusy('hear')
    try {
      const ex = await api.explain(id, lang)
      setReasonLocal(ex.text)
      if (ex.audio_b64) {
        audio.current?.pause()
        audio.current = new Audio(`data:audio/wav;base64,${ex.audio_b64}`)
        await audio.current.play().catch(() => {})
      }
    } finally {
      setBusy(null)
    }
  }

  async function run(name: string, fn: () => Promise<unknown>) {
    setBusy(name)
    setErr('')
    try {
      await fn()
      await load()
      onChanged()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  if (!c) {
    return (
      <>
        <div className="scrim" onClick={onClose} />
        <aside className="drawer" aria-busy="true">
          <div className="drawer-body">{err ? <div className="err">{err}</div> : <Loader2 className="spin" />}</div>
        </aside>
      </>
    )
  }

  const d = c.decision
  const owes = c.dues.reduce((s, x) => s + x.amount_paise, 0)
  const credits = c.credits.reduce((s, x) => s + x.amount_paise, 0)
  const received = c.receipts.reduce((s, x) => s + x.amount_paise, 0)
  const due = d.outstanding_paise
  const total = Math.max(owes, 1)
  const pending = c.approvals.find((a) => a.status === 'pending')
  const link = c.links[0]
  const name = c.account.display_name || c.account.external_ref
  const canLink = d.decision === 'CONTACT' && d.next_actions.includes('request_payment_link')

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className={`drawer s-${d.decision}`} role="dialog" aria-label={name}>
        <div className="drawer-head">
          <span className="dot-icon">
            <StatusIcon d={d.decision} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2>{name}</h2>
            <div className="muted small">
              {c.account.external_ref} · {t.checked} {ago(c.account.provider_checked_at, t)}
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label={t.close}>
            <X size={18} />
          </button>
        </div>

        <div className="drawer-body">
          <section>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <StatusPill d={d.decision} />
              <button className="btn ghost" onClick={hear} disabled={busy === 'hear'}>
                {busy === 'hear' ? <Loader2 size={16} className="spin" /> : <Volume2 size={16} />} {t.hearWhy}
              </button>
            </div>
            <p className="why" style={{ margin: 0 }}>{reasonLocal}</p>
          </section>

          <section aria-label={t.owes}>
            <div className="bar" role="img" aria-label={`${t.paid} ${inr(received)} / ${t.owes} ${inr(owes)}`}>
              <span className="paid" style={{ width: `${Math.min(100, ((owes - due) / total) * 100)}%` }} />
              {d.disputed_paise > 0 && <span className="disputed" style={{ width: `${(d.disputed_paise / total) * 100}%` }} />}
            </div>
            <div className="figures">
              <div>
                <span className="muted small">{t.owes}</span>
                <b className="num">{inr(owes)}</b>
              </div>
              <div>
                <span className="muted small">{t.paid}</span>
                <b className="num" style={{ color: 'var(--call)' }}>{inr(received + credits)}</b>
              </div>
              <div>
                <span className="muted small">{d.disputed_paise ? t.disputed : t.stillDue}</span>
                <b className="num">{inr(d.disputed_paise || due)}</b>
              </div>
            </div>
          </section>

          <section className="next">
            <div className="section-title" style={{ margin: 0 }}>{t.nextStep}</div>
            {canLink && (
              <button
                className="btn big"
                disabled={!!busy}
                onClick={() =>
                  pending
                    ? onApprove(pending, name, d.reason)
                    : run('link', async () => {
                        const res = await api.requestLink(id)
                        if (res.facts.approval) onApprove(res.facts.approval, name, d.reason)
                        else throw new Error(res.summary)
                      })
                }
              >
                <Send size={18} /> {t.sendLink} · <span className="num">{inr(d.collectible_paise)}</span>
              </button>
            )}
            {!canLink && d.decision === 'CONTACT' && <div>{reasonLocal}</div>}
            {d.decision === 'HOLD' && <div>{t.waitNote}</div>}
            {d.decision === 'REVIEW' && <div>{t.reviewNote}</div>}
            {d.decision === 'CLOSED' && <div>{t.noActionNeeded}</div>}
            {d.route_to && (
              <div className="muted">
                {t.passedTo}: <b style={{ color: 'var(--ink)' }}>{d.route_to}</b>
              </div>
            )}
            {d.offered_options.length > 0 && (
              <div>
                <div className="muted small">{t.optionsToOffer}</div>
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                  {d.offered_options.map((o) => (
                    <li key={o}>{o}</li>
                  ))}
                </ul>
              </div>
            )}
            {d.decision === 'CONTACT' && !d.contact_allowed_now && <div className="notice s-HOLD">{t.afterEight}</div>}
            <div>
              <button className="btn ghost" onClick={() => run('refresh', () => api.refresh(id))} disabled={!!busy}>
                {busy === 'refresh' ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />} {t.checkAgain}
              </button>
            </div>
            {err && <div className="err">{err}</div>}
          </section>

          {link && (
            <section>
              <div className="section-title">{t.paymentLink}</div>
              <div className="next" style={{ background: 'transparent' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                  <div>
                    <b>{t[`linkStatus_${link.status}` as Key] ?? link.status}</b>
                    <div className="muted small num">
                      {inr(link.amount_paid_paise)} / {inr(link.amount_paise)} · {link.provenance === 'fixture' ? t.fixture : 'Cashfree'}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {link.provenance === 'fixture' ? (
                      link.amount_paid_paise < link.amount_paise && (
                        <>
                          <button className="btn ghost" onClick={() => run('sim', () => api.simulatePay(link.link_id, String(Math.floor((link.amount_paise - link.amount_paid_paise) / 200))))}>
                            {t.testPay}
                          </button>
                          <button className="btn ghost" onClick={() => run('sim', () => api.simulatePay(link.link_id))}>
                            {t.testPayFull}
                          </button>
                        </>
                      )
                    ) : (
                      link.link_url && (
                        <a className="btn ghost" href={link.link_url} target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none' }}>
                          <ExternalLink size={16} /> {t.openCheckout}
                        </a>
                      )
                    )}
                  </div>
                </div>
              </div>
            </section>
          )}

          <section>
            <div className="section-title">{t.whatWeKnow}</div>
            <ul className="facts">
              {c.receipts.map((r) => (
                <li key={r.provider_payment_id}>
                  <CheckCircle2 size={18} className="ok" />
                  <span>
                    {t.verifiedPayment} <span className="muted small">· {r.provenance === 'fixture' ? t.fixture : 'Cashfree'} · {time(r.verified_at)}</span>
                  </span>
                  <b className="num">{inr(r.amount_paise)}</b>
                </li>
              ))}
              {c.credits.map((cr) => (
                <li key={cr.reference}>
                  <CheckCircle2 size={18} className="ok" />
                  <span>{t.approvedCredit} · {cr.reference}</span>
                  <b className="num">{inr(cr.amount_paise)}</b>
                </li>
              ))}
              {c.claims
                .filter((cl) => cl.kind !== 'salary_date' && !['superseded'].includes(cl.status))
                .map((cl) => (
                  <li key={cl.id}>
                    {cl.status === 'verified' || cl.status === 'kept' ? <CheckCircle2 size={18} className="ok" /> : <MessageSquareQuote size={18} className="warn" />}
                    <span>
                      {t.theySaid}: {CLAIM_WORDS[cl.kind] ?? cl.kind}
                      {cl.promised_date ? ` (${cl.promised_date})` : ''}{' '}
                      <span className="muted small">· {cl.status === 'verified' || cl.status === 'kept' ? t.proven : t.notProven}</span>
                    </span>
                    <b className="num">{cl.amount_paise ? inr(cl.amount_paise) : ''}</b>
                  </li>
                ))}
              {c.dues.map((du) => (
                <li key={du.id}>
                  <span className="muted" style={{ textAlign: 'center' }}>·</span>
                  <span className="muted">
                    {t.bills}: {du.external_ref} {du.due_date ? `· ${du.due_date}` : ''}
                  </span>
                  <span className="num muted">{inr(du.amount_paise)}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <div className="section-title">{t.history}</div>
            <ul className="timeline">
              {c.trace
                .map((tr) => ({ tr, d: describe(tr) }))
                .filter((x) => x.d)
                .slice(0, 14)
                .map(({ tr, d: line }) => (
                  <li key={tr.id}>
                    <time>{time(tr.ts)}</time>
                    <span className={line!.bad ? 'bad' : ''}>
                      {line!.bad && <AlertTriangle size={13} style={{ marginRight: 4, verticalAlign: -2 }} />}
                      {line!.text}
                    </span>
                  </li>
                ))}
            </ul>
          </section>
        </div>
      </aside>
    </>
  )
}
