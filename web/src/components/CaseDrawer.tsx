import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ExternalLink, Loader2, RefreshCw, Send, Volume2, X } from 'lucide-react'
import { api, type Approval, type CaseDetail, type TraceRow } from '../api'
import { errText } from '../errors'
import { ago, inr, time } from '../format'
import { type Key } from '../i18n'
import { usePrefs } from '../prefs'
import { HelpButton } from './HelpButton'
import { useExit } from '../motion'
import { Status } from './Status'

const CLAIM_WORDS: Record<string, string> = {
  paid: 'Says it is already paid',
  promise: 'Promised to pay',
  deduction: 'Wants a deduction',
  dispute: 'Disputes the bill',
  hardship: 'Cannot pay right now',
  opt_out: 'Asked not to be called',
}

const ACTION_WORDS: Record<string, string> = {
  check_payment_reference: 'Checked the payment with Cashfree',
  refresh_account: 'Re-checked payments',
  refresh_link: 'Checked the payment link',
  record_claim: 'Noted what was said',
  allocate_receipts: 'Matched money to bills',
  propose_payment_link: 'Prepared a link for approval',
  deliver_link: 'Sent the link',
}

function describe(tr: TraceRow): { text: string; bad?: boolean } | null {
  const r = (tr.result ?? {}) as Record<string, unknown>
  switch (tr.kind) {
    case 'handoff':
      if (!ACTION_WORDS[tr.allowed_action ?? '']) return null
      if (/^Re-checked 0 /.test(String(r.summary ?? '')) || r.summary === 'Nothing new to allocate') return null
      return { text: `${ACTION_WORDS[tr.allowed_action ?? '']}: ${r.summary ?? ''}` }
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
      return { text: 'Ignored a message that was not really from Cashfree', bad: true }
    default:
      return null
  }
}

let audio: HTMLAudioElement | null = null

export function CaseDrawer({ id, onClose, onChanged, onApprove }: {
  id: string
  onClose: () => void
  onChanged: () => void
  onApprove: (a: Approval, name: string, reason: string) => void
}) {
  const { t, lang } = usePrefs()
  const [c, setC] = useState<CaseDetail | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const panel = useRef<HTMLElement>(null)
  const [closing, close] = useExit(onClose)

  const load = useCallback(async () => {
    const [detail, ex] = await Promise.all([
      api.caseDetail(id),
      lang === 'en-IN' ? Promise.resolve(null) : api.explain(id, lang).catch(() => null),
    ])
    setC(detail)
    setReason(ex?.text ?? detail.decision.reason)
  }, [id, lang])

  useEffect(() => {
    load().catch((e) => setErr(errText(e, t)))
    const timer = window.setInterval(() => load().catch(() => {}), 5000)
    return () => window.clearInterval(timer)
  }, [load, t])

  useEffect(() => {
    panel.current?.focus() // move focus into the dialog without drawing a ring on a button
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  async function hear() {
    setBusy('hear')
    try {
      const ex = await api.explain(id, lang)
      setReason(ex.text)
      if (ex.audio_b64) {
        audio?.pause()
        audio = new Audio(`data:audio/wav;base64,${ex.audio_b64}`)
        await audio.play().catch(() => {})
      }
    } catch (e) {
      setErr(errText(e, t))
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
      setErr(errText(e, t))
    } finally {
      setBusy(null)
    }
  }

  /** "Note in borrower_batch_A.csv" / "Said to the assistant by meena". */
  function sourceOf(src: string): string {
    const [kind, sub, batch] = src.split(':')
    if (kind === 'import') {
      const file = c?.batch_files[batch ?? ''] ?? batch ?? ''
      return `${sub === 'payment_ref' ? t.fromRef : t.fromNote} ${file}`
    }
    if (kind === 'voice') return `${t.fromVoice} ${sub}`
    return src
  }

  const shell = (body: React.ReactNode) => (
    <>
      <div className={`overlay ${closing ? 'closing' : ''}`} onClick={close} />
      <aside ref={panel} tabIndex={-1} className={`drawer ${closing ? 'closing' : ''}`} role="dialog" aria-modal="true" aria-label={c?.account.display_name ?? ''}>
        <div className="drawer-top">
          <span>{c && <Status d={c.decision.decision} />}</span>
          <button className="icon-btn" onClick={close} aria-label={t.close}>
            <X size={18} strokeWidth={1.8} />
          </button>
        </div>
        <div className="drawer-body">{body}</div>
      </aside>
    </>
  )

  if (!c) return shell(err ? <p className="err">{err}</p> : <Loader2 className="spin" />)

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
  const claims = c.claims.filter((cl) => cl.kind !== 'salary_date' && cl.status !== 'superseded')
  const history = c.trace.map((tr) => ({ tr, line: describe(tr) })).filter((x) => x.line).slice(0, 12)

  return shell(
    <>
      <h2>{name}</h2>
      <div className="meta">
        {c.account.external_ref} · {t.checked} {ago(c.account.provider_checked_at, t)}
      </div>

      <p className="lead">{reason}</p>
      <div className="listen">
        <button className="btn text" onClick={hear} disabled={busy === 'hear'}>
          {busy === 'hear' ? <Loader2 size={15} className="spin" /> : <Volume2 size={15} strokeWidth={1.8} />} {t.hearWhy}
        </button>
      </div>

      <div className="figures">
        <div>
          <div className="overline">{t.owes}</div>
          <b>{inr(owes)}</b>
        </div>
        <div>
          <div className="overline">{t.paid}</div>
          <b className={received + credits > 0 ? 'good' : ''}>{inr(received + credits)}</b>
        </div>
        <div>
          <div className="overline">{d.disputed_paise ? t.disputed : t.stillDue}</div>
          <b>{inr(d.disputed_paise || due)}</b>
        </div>
      </div>
      <div className="meter" role="img" aria-label={`${t.paid} ${inr(owes - due)} / ${inr(owes)}`}>
        <span className="paid" style={{ width: `${Math.min(100, ((owes - due) / total) * 100)}%` }} />
        {d.disputed_paise > 0 && <span className="disputed" style={{ width: `${(d.disputed_paise / total) * 100}%` }} />}
      </div>

      <div className="block action">
        <div className="overline">{t.nextStep}</div>
        {canLink && (
          <div className="action-row">
            <button
              className="btn lg"
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
              {busy === 'link' ? <Loader2 size={17} className="spin" /> : <Send size={17} strokeWidth={1.9} />}
              {t.sendLink} · <span className="num">{inr(d.collectible_paise)}</span>
            </button>
            <HelpButton topic="send_link" label={t.sendLink} />
          </div>
        )}
        {d.decision === 'HOLD' && <p className="note">{t.waitNote}</p>}
        {d.decision === 'REVIEW' && <p className="note">{t.reviewNote}</p>}
        {d.decision === 'CLOSED' && <p className="note">{t.noActionNeeded}</p>}
        {d.route_to && (
          <div className="owner">
            {t.passedTo} <b>{d.route_to}</b>
          </div>
        )}
        {d.offered_options.length > 0 && (
          <div>
            <div className="owner">{t.optionsToOffer}</div>
            <ul className="options">
              {d.offered_options.map((o) => (
                <li key={o}>{o}</li>
              ))}
            </ul>
          </div>
        )}
        {d.decision === 'CONTACT' && !d.contact_allowed_now && <div className="callout s-HOLD">{t.afterEight}</div>}
        <div className="action-row">
          <button className="btn secondary" onClick={() => run('refresh', () => api.refresh(id))} disabled={!!busy}>
            {busy === 'refresh' ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} strokeWidth={1.8} />} {t.checkAgain}
          </button>
          <HelpButton topic="check_again" label={t.checkAgain} />
        </div>
        {err && <p className="err">{err}</p>}
      </div>

      {link && (
        <div className="block">
          <div className="overline">{t.paymentLink}</div>
          <div className="linkbox">
            <div>
              <div className="title">{t[`linkStatus_${link.status}` as Key] ?? link.status}</div>
              <div className="muted small num">
                {inr(link.amount_paid_paise)} / {inr(link.amount_paise)} · {link.provenance === 'fixture' ? t.fixture : 'Cashfree'}
              </div>
            </div>
            <div className="action-row">
              {link.provenance === 'fixture'
                ? link.amount_paid_paise < link.amount_paise && (
                    <>
                      <button className="btn secondary" onClick={() => run('sim', () => api.simulatePay(link.link_id, String(Math.floor((link.amount_paise - link.amount_paid_paise) / 200))))}>
                        {t.testPay}
                      </button>
                      <button className="btn secondary" onClick={() => run('sim', () => api.simulatePay(link.link_id))}>
                        {t.testPayFull}
                      </button>
                    </>
                  )
                : link.link_url && (
                    <a className="btn secondary" href={link.link_url} target="_blank" rel="noopener noreferrer">
                      <ExternalLink size={15} strokeWidth={1.8} /> {t.openCheckout}
                    </a>
                  )}
            </div>
          </div>
        </div>
      )}

      <div className="block">
        <div className="overline">{t.whatWeKnow}</div>
        <ul className="facts">
          {c.receipts.map((r) => (
            <li key={r.provider_payment_id}>
              <span className="mk ok" />
              <span>
                {t.verifiedPayment}
                <div className="sub">
                  {r.provenance === 'fixture' ? t.fixture : 'Cashfree'} · {r.provider_payment_id} · {time(r.verified_at)}
                </div>
              </span>
              <span className="amt">{inr(r.amount_paise)}</span>
            </li>
          ))}
          {c.credits.map((cr) => (
            <li key={cr.reference}>
              <span className="mk ok" />
              <span>
                {t.approvedCredit}
                <div className="sub">{cr.reference}</div>
              </span>
              <span className="amt">{inr(cr.amount_paise)}</span>
            </li>
          ))}
          {claims.map((cl) => {
            const proven = cl.status === 'verified' || cl.status === 'kept'
            return (
              <li key={cl.id}>
                <span className={`mk ${proven ? 'ok' : 'said'}`} />
                <span>
                  {CLAIM_WORDS[cl.kind] ?? cl.kind}
                  {cl.promised_date ? ` · ${cl.promised_date}` : ''}
                  <span className="muted"> · {proven ? t.proven : t.notProven}</span>
                  {cl.text && (cl.source.startsWith('import:note') || cl.source.startsWith('voice')) ? (
                    <div className="sub" style={{ color: 'var(--ink-2)', fontStyle: 'italic' }}>“{cl.text}”</div>
                  ) : null}
                  {cl.reference && <div className="sub">Ref {cl.reference}{cl.status_reason ? `: ${cl.status_reason}` : ''}</div>}
                  <div className="sub">{sourceOf(cl.source)}</div>
                </span>
                <span className="amt">{cl.amount_paise ? inr(cl.amount_paise) : ''}</span>
              </li>
            )
          })}
          {c.dues.map((du) => (
            <li key={du.id}>
              <span className="mk bill" />
              <span className="muted">
                {t.bills} · {du.external_ref}
                {du.due_date ? ` · ${du.due_date}` : ''}
              </span>
              <span className="amt muted">{inr(du.amount_paise)}</span>
            </li>
          ))}
        </ul>
      </div>

      {history.length > 0 && (
        <div className="block">
          <div className="overline">{t.history}</div>
          <ul className="timeline">
            {history.map(({ tr, line }) => (
              <li key={tr.id}>
                <time>{time(tr.ts)}</time>
                <span className={line!.bad ? 'bad' : ''}>
                  {line!.bad && <AlertTriangle size={13} style={{ marginRight: 4, verticalAlign: -2 }} />}
                  {line!.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>,
  )
}
