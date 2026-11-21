import type { Decision, QueueChange } from '../api'
import { time } from '../format'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'

export function ChangeFeed({ items, onOpen }: { items: QueueChange[]; onOpen: (id: string) => void }) {
  const { t } = usePrefs()
  return (
    <section className="panel" aria-label={t.changes}>
      <h2>{t.changes}</h2>
      {items.length === 0 ? (
        <p className="quiet">{t.noChanges}</p>
      ) : (
        <ul className="feed">
          {items.slice(0, 6).map((c, i) => (
            <li key={`${c.account_id}-${c.at}-${i}`}>
              <time>{time(c.at)}</time>
              <span>
                <button className="link" onClick={() => onOpen(c.account_id)}>
                  {c.external_ref}
                </button>{' '}
                <span className="muted">{groupLabel(t, c.from as Decision)} →</span>{' '}
                <span className={`status s-${c.to}`} style={{ fontSize: 14 }}>
                  {groupLabel(t, c.to as Decision)}
                </span>
                <div className="why">{c.why}</div>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
