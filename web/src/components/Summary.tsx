import type { Decision, Queue } from '../api'
import { inr } from '../format'
import { groupLabel, type Key } from '../i18n'
import { usePrefs } from '../prefs'
import { GROUPS, StatusIcon } from './Status'
import { HelpButton } from './HelpButton'

export function Summary({ q, filter, setFilter }: { q: Queue; filter: Decision | null; setFilter: (d: Decision | null) => void }) {
  const { t } = usePrefs()
  const amountFor = (d: Decision) => q.rows.filter((r) => r.decision === d).reduce((s, r) => s + r.outstanding_paise, 0)
  return (
    <>
      <section className="money" aria-label="Money">
        <div className="card" style={{ position: 'relative' }}>
          <span style={{ position: 'absolute', top: 14, right: 14 }}>
            <HelpButton topic="money" label={t.stillToCollect} />
          </span>
          <div className="label">{t.stillToCollect}</div>
          <div className="value num">{inr(q.totals.outstanding_paise)}</div>
          <div className="hint">
            {t.canAskToday}: <b className="num">{inr(q.totals.collectible_now_paise)}</b>
          </div>
        </div>
        <div className="card">
          <div className="label">{t.collected}</div>
          <div className="value num" style={{ color: 'var(--call)' }}>
            {inr(q.totals.received_paise)}
          </div>
          <div className="hint">{t.collectedHint}</div>
        </div>
        <div className="card">
          <div className="label">{t.onHold}</div>
          <div className="value num">{inr(q.totals.on_hold_paise)}</div>
          <div className="hint">
            {groupLabel(t, 'HOLD')} + {groupLabel(t, 'REVIEW')}
          </div>
        </div>
      </section>
      <section className="groups" aria-label="Groups">
        {GROUPS.map((g) => (
          <div key={g} className="group-wrap">
          <span className="group-help">
            <HelpButton topic={`group_${g}`} label={groupLabel(t, g)} />
          </span>
          <button className={`group s-${g}`} aria-pressed={filter === g} onClick={() => setFilter(filter === g ? null : g)}>
            <span className="dot-icon">
              <StatusIcon d={g} />
            </span>
            <span>
              <span className="count num">{q.counts[g]}</span>
              <span className="name" style={{ display: 'block' }}>{groupLabel(t, g)}</span>
              <span className="muted num small" style={{ display: 'block' }}>{inr(amountFor(g))}</span>
              <span className="hint" style={{ display: 'block' }}>
                {t[`groupHint_${g}` as Key]}
              </span>
            </span>
          </button>
          </div>
        ))}
      </section>
    </>
  )
}
