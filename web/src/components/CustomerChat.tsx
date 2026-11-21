import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowUp, Loader2, Mic, Paperclip } from 'lucide-react'
import { api, type ChatMessage, type ChatThread, type Queue } from '../api'
import { errText } from '../errors'
import { inr, time } from '../format'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'
import { shrink } from '../image'
import { Dot } from './Status'

const CLAIM_LABEL: Record<string, string> = {
  paid: 'Says paid', promise: 'Promise', deduction: 'Deduction', hardship: 'Hardship', opt_out: 'Do not call',
}

/** Plain text with http(s) links made tappable, like a messaging app. */
function Linked({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s]+?)(?=[.,;:!?)]?(?:\s|$))/g)
  return (
    <>
      {parts.map((p, i) =>
        /^https?:\/\//.test(p) ? (
          <a key={i} href={p} target="_blank" rel="noopener noreferrer" className="bubble-link">
            {p}
          </a>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  )
}

type Local = ChatMessage & { pending?: boolean; audioUrl?: string; imageUrl?: string; replyAudio?: string | null }

/** The customer's side of the conversation, simulated in the dashboard (replaces WhatsApp). */
export function CustomerChat({ q, onChanged }: { q: Queue | null; onChanged: () => void }) {
  const { t } = usePrefs()
  const people = q?.rows ?? []
  const [active, setActive] = useState<string | null>(null)
  const [thread, setThread] = useState<ChatThread | null>(null)
  const [msgs, setMsgs] = useState<Local[]>([])
  const [text, setText] = useState('')
  const [state, setState] = useState<'idle' | 'rec' | 'busy'>('idle')
  const [err, setErr] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const picker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!active && people.length) setActive(people[0].account_id)
  }, [people, active])

  useEffect(() => {
    if (!active) return
    setErr('')
    api.inboxThread(active).then((th) => {
      setThread(th)
      setMsgs(th.messages)
    }).catch((e) => setErr(errText(e, t)))
  }, [active, t])

  useEffect(() => {
    const box = end.current?.parentElement
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' })
  }, [msgs])

  async function send(body: { text?: string; audio?: Blob; image?: Blob }) {
    if (!active || state === 'busy') return
    const audioUrl = body.audio ? URL.createObjectURL(body.audio) : undefined
    const imageUrl = body.image ? URL.createObjectURL(body.image) : undefined
    const mine: Local = { ts: new Date().toISOString(), via: body.image ? 'image' : body.audio ? 'voice' : 'text', text: body.text ?? null,
      reply: null, reply_source: null, claims: [], status: null, language: null, pending: true, audioUrl, imageUrl }
    setMsgs((m) => [...m, mine])
    setText('')
    setState('busy')
    setErr('')
    try {
      const r = await api.inboxSend(active, body.text ?? null, body.audio ?? null, body.image ?? null)
      setMsgs((m) => m.map((x) => (x === mine ? { ...x, pending: false, text: r.transcript || x.text, reply: r.reply ?? r.note ?? '',
        reply_source: r.reply_source, claims: r.claims.map((c) => c.kind), status: r.decision?.code ?? null,
        replyAudio: r.reply_audio_b64 } : x)))
      if (r.reply_audio_b64) new Audio(`data:audio/wav;base64,${r.reply_audio_b64}`).play().catch(() => {})
      api.inboxThread(active).then(setThread).catch(() => {})
      onChanged()
    } catch (e) {
      setMsgs((m) => m.filter((x) => x !== mine))
      setErr(errText(e, t))
    } finally {
      setState('idle')
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    if (text.trim()) send({ text: text.trim() })
  }

  async function startRec() {
    if (state !== 'idle') return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(stream)
      chunks.current = []
      r.ondataavailable = (e) => chunks.current.push(e.data)
      r.onstop = () => {
        stream.getTracks().forEach((tr) => tr.stop())
        const blob = new Blob(chunks.current, { type: r.mimeType || 'audio/webm' })
        setState('idle')
        if (blob.size > 2000) send({ audio: blob })
      }
      rec.current = r
      r.start()
      setState('rec')
    } catch {
      setErr(t.micDenied)
    }
  }
  async function pickPhoto(f: File | undefined) {
    if (!f) return
    try {
      send({ image: await shrink(f), text: text.trim() || undefined })
    } catch {
      setErr(t.photoUnreadable)
    } finally {
      if (picker.current) picker.current.value = ''
    }
  }
  const stopRec = () => rec.current?.state === 'recording' && rec.current.stop()

  const person = people.find((p) => p.account_id === active)

  return (
    <>
      <div className="page-head">
        <div>
          <div className="overline">{t.chatHint}</div>
          <h1>{t.chatTitle}</h1>
        </div>
      </div>
      <div className="chat-app">
        <aside className="chat-list" aria-label={t.pickCustomer}>
          {people.map((p) => (
            <button key={p.account_id} className="chat-person" aria-current={p.account_id === active} onClick={() => setActive(p.account_id)}>
              <span className="avatar">{(p.display_name || p.external_ref).split(' ').map((w) => w[0]).slice(0, 2).join('')}</span>
              <span style={{ minWidth: 0 }}>
                <span className="who">{p.display_name || p.external_ref}</span>
                <span className="ref">
                  <Dot d={p.decision} /> {p.external_ref} · {groupLabel(t, p.decision)}
                </span>
              </span>
              <span className="num small muted">{p.decision === 'CLOSED' ? inr(0) : inr(p.outstanding_paise)}</span>
            </button>
          ))}
        </aside>

        <section className="chat-pane" aria-label={t.chatTitle}>
          {person && (
            <header className="chat-head">
              <span className="avatar">{(person.display_name || person.external_ref).split(' ').map((w) => w[0]).slice(0, 2).join('')}</span>
              <div style={{ minWidth: 0 }}>
                <div className="who">{person.display_name || person.external_ref}</div>
                <div className="ref">
                  {person.external_ref} · {thread?.facts.amount_due ?? ''} · {groupLabel(t, person.decision)}
                </div>
              </div>
            </header>
          )}
          <div className="chat-scroll">
            {msgs.length === 0 && <div className="chat-empty">{t.noMessages}</div>}
            {msgs.map((m, i) => (
              <div key={`${m.ts}-${i}`} className="chat-pair">
                <div className="bubble me">
                  {m.via === 'voice' && (
                    <div className="voice-tag">
                      <Mic size={12} /> {t.voiceNote}
                    </div>
                  )}
                  {m.audioUrl && <audio controls src={m.audioUrl} className="bubble-audio" />}
                  {m.imageUrl && <img src={m.imageUrl} alt={t.photo} className="bubble-img" />}
                  {m.via === 'image' ? (
                    !m.pending && m.text && (
                      <div className="ocr-read">
                        <div className="voice-tag">{t.readFromPhoto}</div>
                        {m.text}
                      </div>
                    )
                  ) : (
                    m.text && <div>{m.text}</div>
                  )}
                  <span className="stamp">{time(m.ts)}</span>
                </div>
                {m.pending ? (
                  <div className="bubble them typing">
                    <Loader2 size={14} className="spin" /> {t.lenderTyping}
                  </div>
                ) : (
                  m.reply && (
                    <div className="bubble them">
                      <div>
                        <Linked text={m.reply} />
                      </div>
                      {m.replyAudio && <audio controls src={`data:audio/wav;base64,${m.replyAudio}`} className="bubble-audio" />}
                      {m.claims.length > 0 && (
                        <div className="chips-row">
                          {m.claims.map((c) => (
                            <span key={c} className="chip-note">
                              {t.recordedClaim}: {CLAIM_LABEL[c] ?? c}
                            </span>
                          ))}
                        </div>
                      )}
                      <span className="stamp">{time(m.ts)}</span>
                    </div>
                  )
                )}
              </div>
            ))}
            <div ref={end} />
          </div>
          {err && <p className="err" style={{ margin: '0 16px 8px' }}>{err}</p>}
          <form className="chat-input" onSubmit={submit}>
            <input ref={picker} type="file" accept="image/*,application/pdf" hidden onChange={(e) => pickPhoto(e.target.files?.[0])} />
            <button type="button" className="btn round ghost" aria-label={t.sendPhoto} title={t.sendPhoto}
              disabled={!active || state !== 'idle'} onClick={() => picker.current?.click()}>
              <Paperclip size={18} strokeWidth={2} />
            </button>
            <input className="field" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.typeMessage} aria-label={t.typeMessage}
              maxLength={500} disabled={!active} />
            {text.trim() ? (
              <button className="btn round" disabled={state !== 'idle'} aria-label={t.send}>
                <ArrowUp size={18} strokeWidth={2.2} />
              </button>
            ) : (
              <button type="button" className={`btn round ${state === 'rec' ? 'rec' : ''}`} aria-label={t.holdForVoice} title={t.holdForVoice}
                disabled={!active || state === 'busy'} onPointerDown={startRec} onPointerUp={stopRec} onPointerLeave={stopRec}>
                {state === 'busy' ? <Loader2 size={18} className="spin" /> : <Mic size={18} strokeWidth={2} />}
              </button>
            )}
          </form>
        </section>
      </div>
    </>
  )
}
