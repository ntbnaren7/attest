import { useCallback, useRef, useState } from 'react'
import { flushSync } from 'react-dom'

const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/**
 * Run a state change inside a View Transition so the page crossfades (and named elements such as the
 * tab underline morph) instead of jumping. Browsers without the API, or users who prefer reduced
 * motion, get the change instantly.
 */
export function transition(update: () => void) {
  type VT = { ready: Promise<void>; finished: Promise<void>; updateCallbackDone: Promise<void> }
  const doc = document as Document & { startViewTransition?: (cb: () => void) => VT }
  if (!doc.startViewTransition || reduced()) {
    update()
    return
  }
  const vt = doc.startViewTransition(() => flushSync(update))
  // A newer transition skips this one; that is expected, not an error.
  const ignore = () => {}
  vt.ready.catch(ignore)
  vt.finished.catch(ignore)
  vt.updateCallbackDone.catch(ignore)
}

/** Duration of exit animations for drawers and dialogs (keep in sync with styles.css). */
export const EXIT_MS = 170


/** Lets an overlay play its exit animation before it unmounts. */
export function useExit(onClose: () => void): [boolean, () => void] {
  const [closing, setClosing] = useState(false)
  const done = useRef(false)
  const close = useCallback(() => {
    if (done.current) return
    done.current = true
    if (reduced()) return onClose()
    setClosing(true)
    window.setTimeout(onClose, EXIT_MS)
  }, [onClose])
  return [closing, close]
}
