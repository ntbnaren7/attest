import { CheckCircle2, Clock3, PhoneCall, ShieldAlert } from 'lucide-react'
import type { Decision } from '../api'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'

export const GROUPS: Decision[] = ['CONTACT', 'HOLD', 'REVIEW', 'CLOSED']

export function StatusIcon({ d, size = 20 }: { d: Decision; size?: number }) {
  const props = { size, strokeWidth: 2.2, 'aria-hidden': true }
  if (d === 'CONTACT') return <PhoneCall {...props} />
  if (d === 'HOLD') return <Clock3 {...props} />
  if (d === 'REVIEW') return <ShieldAlert {...props} />
  return <CheckCircle2 {...props} />
}

export function StatusPill({ d }: { d: Decision }) {
  const { t } = usePrefs()
  return (
    <span className={`pill s-${d}`}>
      <StatusIcon d={d} size={15} />
      {groupLabel(t, d)}
    </span>
  )
}
