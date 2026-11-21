import type { Decision } from '../api'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'

export const GROUPS: Decision[] = ['CONTACT', 'HOLD', 'REVIEW', 'CLOSED']

export function Dot({ d }: { d: Decision }) {
  return <span className={`dot s-${d}`} aria-hidden="true" />
}

export function Status({ d }: { d: Decision }) {
  const { t } = usePrefs()
  return (
    <span className={`status s-${d}`}>
      <span className="dot" aria-hidden="true" />
      {groupLabel(t, d)}
    </span>
  )
}

/** Two parallel bars: the thing that stays equal on both sides of the books. */
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <rect width="20" height="20" rx="5.5" fill="currentColor" />
      <rect x="5" y="7" width="10" height="2" rx="1" fill="var(--bg)" />
      <rect x="5" y="11" width="10" height="2" rx="1" fill="var(--bg)" />
    </svg>
  )
}
