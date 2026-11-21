import { useRef, useState, type FormEvent } from 'react'
import { ArrowUp, Ban, Check, Loader2, Mic } from 'lucide-react'
import { api, type Approval, type Lang, type Turn } from '../api'
import { DICTS, LANG_LABEL } from '../i18n'
import { errText } from '../errors'
import { usePrefs } from '../prefs'
import { HelpButton } from './HelpButton'

const STEP_WORDS: Record<string, string> = {
  check_payment_reference: 'Checked the payment with Cashfree',
  refresh_account: 'Re-checked payments',
  refresh_link: 'Checked the payment link',
  record_claim: 'Noted what was said (not a fact yet)',
  allocate_receipts: 'Matched money to bills',
  evaluate_case: 'Looked at the records',
  propose_payment_link: 'Prepared a link for your approval',
  hold_outreach: 'Paused reminders',
  deliver_link: 'Sent the link',
}

export function Assistant({ voiceOn, focusId, onCase, onApprove, onChanged }: {
  voiceOn: boolean
  focusId: string | null
  onCase: (id: string) => void
  onApprove: (a: Approval, name: string) => void
  onChanged: () => void
}) {
  const { t, lang } = usePrefs()
  const [speakLang, setSpeakLang] = useState<Lang | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [state, setState] = useState<'idle' | 'rec' | 'busy'>('idle')
  const [err, setErr] = useState('')
  const conv = useRef<string | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const examples = DICTS[speakLang ?? lang].examples

  function took(turn: Turn) {
    conv.current = turn.conversation_id
    setTurns((xs) => [turn, ...xs].slice(0, 12))
    if (turn.reply_audio_b64) new Audio(`data:audio/wav;base64,${turn.reply_audio_b64}`).play().catch(() => {})
    if (turn.account) onCase(turn.account.id)
    if (turn.approval_request && turn.account) onApprove(turn.approval_request, turn.account.external_ref)
    onChanged()
  }

  async function ask(q: string) {
    if (state !== 'idle' || !q.trim()) return
    setState('busy')
    setErr('')
    try {
      took(await api.say(q.trim(), speakLang ?? lang, conv.current, focusId))
      setText('')
    } catch (x) {
      setErr(errText(x, t))
    } finally {
      setState('idle')
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    ask(text)
  }

  async function start() {
    if (state !== 'idle' || !voiceOn) return
    setErr('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(stream)
      chunks.current = []
      r.ondataavailable = (e) => chunks.current.push(e.data)
      r.onstop = async () => {
        stream.getTracks().forEach((tr) => tr.stop())
        const blob = new Blob(chunks.current, { type: r.mimeType || 'audio/webm' })
        if (blob.size < 2000) {
          setState('idle')
          return
        }
        setState('busy')
        try {
          took(await api.speak(blob, speakLang, conv.current, focusId))
        } catch (x) {
          setErr(errText(x, t))
        } finally {
          setState('idle')
        }
      }
      rec.current = r
      r.start()
      setState('rec')
    } catch {
      setErr(t.micDenied)
    }
  }

  function stop() {
    if (rec.current?.state === 'recording') rec.current.stop()
  }

  return (
    <section className="panel" aria-label={t.assistant}>
      <h2>{t.assistant}</h2>
      <p className="lede">{t.assistantHint}</p>

      <div className="segmented langs" role="group" aria-label={t.language}>
        <button type="button" aria-pressed={speakLang === null} onClick={() => setSpeakLang(null)}>
          {t.auto}
        </button>
        {(Object.keys(LANG_LABEL) as Lang[]).map((l) => (
          <button key={l} type="button" aria-pressed={speakLang === l} onClick={() => setSpeakLang(l)}>
            {LANG_LABEL[l]}
          </button>
        ))}
      </div>

      <div className="speak-row">
        <button
          className={`mic ${state === 'rec' ? 'rec' : ''}`}
          disabled={!voiceOn || state === 'busy'}
          onPointerDown={start}
          onPointerUp={stop}
          onPointerLeave={stop}
          onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && !e.repeat && start()}
          onKeyUp={(e) => (e.key === ' ' || e.key === 'Enter') && stop()}
        >
          {state === 'busy' ? <Loader2 size={20} className="spin" /> : state === 'rec' ? <span className="pulse" /> : <Mic size={20} strokeWidth={1.9} />}
          {state === 'rec' ? t.listening : state === 'busy' ? t.thinking : t.holdToSpeak}
        </button>
        <HelpButton topic="speak" label={t.holdToSpeak} />
      </div>
      {state === 'rec' && (
        <div className="say-like" aria-live="polite">
          {t.sayLike}: <b>“{examples[0]}”</b>
        </div>
      )}
      {!voiceOn && <p className="quiet small">{t.voiceOffNote}</p>}

      <form className="ask" onSubmit={submit}>
        <input className="field" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.typeHere} aria-label={t.typeHere} maxLength={500} />
        <button className="btn send" disabled={state !== 'idle' || !text.trim()} aria-label={t.send}>
          <ArrowUp size={18} strokeWidth={2.2} />
        </button>
      </form>
      {err && <p className="err">{err}</p>}

      {turns.length < 2 && (
        <div className="examples">
          <div className="overline">{t.tryAsking}</div>
          <ul>
            {examples.map((ex) => (
              <li key={ex}>
                <button type="button" className="example" onClick={() => ask(ex)} disabled={state !== 'idle'}>
                  {ex}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="chat" aria-live="polite">
        {turns.map((tu, i) => (
          <div key={`${tu.conversation_id}-${i}`}>
            <div className="q">
              {tu.via === 'voice' && <Mic size={12} />}
              {tu.transcript}
            </div>
            <div className={`a ${tu.denials.length ? 'denied' : ''}`}>{tu.reply}</div>
            {(tu.handoffs.length > 0 || tu.denials.length > 0) && (
              <details>
                <summary>
                  {t.whatIChecked} · {tu.extraction.source === 'sarvam_tool_call' ? 'Sarvam' : 'rules'}
                </summary>
                <ul className="steps">
                  {tu.handoffs.map((h, j) => (
                    <li key={j}>
                      <Check size={13} style={{ flex: 'none', marginTop: 3 }} />
                      <span>
                        {STEP_WORDS[h.action] ?? h.action}
                        {h.action !== 'evaluate_case' && h.action !== 'hold_outreach' ? `: ${h.summary}` : ''}
                      </span>
                    </li>
                  ))}
                  {tu.denials.map((dn, j) => (
                    <li key={`d${j}`} style={{ color: 'var(--check)' }}>
                      <Ban size={13} style={{ flex: 'none', marginTop: 3 }} />
                      <span>{dn.reason}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
