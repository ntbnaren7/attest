import { ChevronRight } from 'lucide-react'
import type { Decision, QueueRow } from '../api'
import { ago, inr } from '../format'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'
import { StatusIcon } from './Status'

export function PeopleList({ rows, filter, flash, onOpen, onClear }: {
  rows: QueueRow[]
  filter: Decision | null
  flash: Set<string>
  onOpen: (id: string) => void
  onClear: () => void
}) {
  const { t } = usePrefs()
  const shown = filter ? rows.filter((r) => r.decision === filter) : rows
  return (
    <section className="card" aria-label="People">
      <div className="list-head">
        <h2>
          {filter ? groupLabel(t, filter) : t.all}{' '}
          <span className="muted num" style={{ fontWeight: 400 }}>
            · {shown.length} {shown.length === 1 ? t.person : t.people}
          </span>
        </h2>
        {filter && (
          <button className="btn quiet" onClick={onClear}>
            {t.all}
          </button>
        )}
      </div>
      {shown.length === 0 && <div className="empty">{rows.length ? t.empty : t.emptyStart}</div>}
      {shown.map((r) => (
        <button key={r.account_id} className={`row s-${r.decision} ${flash.has(r.account_id) ? 'flash' : ''}`} onClick={() => onOpen(r.account_id)}>
          <span className="rank num">{r.rank}</span>
          <span className="dot-icon" title={groupLabel(t, r.decision)}>
            <StatusIcon d={r.decision} />
          </span>
          <span style={{ minWidth: 0 }}>
            <span className="who">
              {r.display_name || r.external_ref}
              <span className="ref">{r.external_ref}</span>
            </span>
            <span className="reason" style={{ display: '-webkit-box' }}>
              {r.reason_local}
            </span>
          </span>
          <span className="right">
            <span className="amount num" style={{ display: 'block' }}>
              {inr(r.decision === 'CLOSED' ? 0 : r.outstanding_paise)}
            </span>
            <span className="meta" style={{ display: 'block' }}>
              {r.decision === 'CONTACT' && !r.contact_allowed_now ? t.afterEight : `${t.checked} ${ago(r.last_verified_at, t)}`}
            </span>
          </span>
          <ChevronRight className="sr" />
        </button>
      ))}
    </section>
  )
}
