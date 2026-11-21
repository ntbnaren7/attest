import type { Decision, QueueChange } from '../api'
import { time } from '../format'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'

export function ChangeFeed({ items, onOpen }: { items: QueueChange[]; onOpen: (id: string) => void }) {
  const { t } = usePrefs()
  return (
    <section className="card panel" aria-label={t.changes}>
      <h2>{t.changes}</h2>
      {items.length === 0 && <p className="muted small" style={{ margin: '6px 0 0' }}>{t.noChanges}</p>}
      <ul className="feed">
        {items.slice(0, 8).map((c, i) => (
          <li key={`${c.account_id}-${c.at}-${i}`} className={`s-${c.to}`}>
            <i />
            <span>
              <button className="btn quiet" style={{ height: 'auto', padding: 0, fontWeight: 700 }} onClick={() => onOpen(c.account_id)}>
                {c.external_ref}
              </button>{' '}
              {groupLabel(t, c.from as Decision)} → <b>{groupLabel(t, c.to as Decision)}</b>{' '}
              <span className="muted small">· {time(c.at)}</span>
              <span className="muted small" style={{ display: 'block' }}>{c.why}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
