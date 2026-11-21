import { useEffect, useRef, useState } from 'react'
import { Loader2, Volume2 } from 'lucide-react'
import { api } from '../api'
import { usePrefs } from '../prefs'
import { errText } from '../errors'

export type HelpTopic = 'speak' | 'send_link' | 'approve' | 'check_again' | 'add_list' | 'hear_why' | 'money' | 'group_CONTACT' | 'group_HOLD' | 'group_REVIEW' | 'group_CLOSED'

let current: HTMLAudioElement | null = null

/** Small speaker next to an action: shows what it does and reads it aloud in the chosen language. */
export function HelpButton({ topic, label }: { topic: HelpTopic; label?: string }) {
  const { t, lang } = usePrefs()
  const [text, setText] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!text) return
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setText(null)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setText(null)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [text])

  async function explain(e: React.MouseEvent) {
    e.stopPropagation()
    e.preventDefault()
    setBusy(true)
    try {
      const r = await api.help(topic, lang)
      setText(r.text)
      if (r.audio_b64) {
        current?.pause()
        current = new Audio(`data:audio/wav;base64,${r.audio_b64}`)
        current.play().catch(() => {})
      }
    } catch (err) {
      setText(errText(err, t))
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="help" ref={box}>
      <button type="button" className="help-btn" onClick={explain} aria-label={`${t.explainThis}${label ? `: ${label}` : ''}`} title={t.explainThis}>
        {busy ? <Loader2 size={15} className="spin" /> : <Volume2 size={15} />}
      </button>
      {text && (
        <span className="help-pop" role="status">
          {text}
        </span>
      )}
    </span>
  )
}
