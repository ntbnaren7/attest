import type { Queue } from '../api'
import { inr } from '../format'
import { groupLabel } from '../i18n'
import { usePrefs } from '../prefs'
import { HelpButton } from './HelpButton'

/** Three figures that answer "how much, how much in, how much waiting". */
export function Ledger({ q }: { q: Queue }) {
  const { t } = usePrefs()
  return (
    <section className="ledger" aria-label={t.stillToCollect}>
      <div>
        <div className="overline">{t.stillToCollect}</div>
        <div className="figure">{inr(q.totals.outstanding_paise)}</div>
        <div className="footnote">
          {t.canAskToday} <span className="num" style={{ color: 'var(--ink)', fontWeight: 600 }}>{inr(q.totals.collectible_now_paise)}</span>
        </div>
      </div>
      <div>
        <div className="overline">{t.collected}</div>
        <div className="figure good">{inr(q.totals.received_paise)}</div>
        <div className="footnote">{t.collectedHint}</div>
      </div>
      <div>
        <div className="overline">{t.onHold}</div>
        <div className="figure">{inr(q.totals.on_hold_paise)}</div>
        <div className="footnote">
          {groupLabel(t, 'HOLD')} · {groupLabel(t, 'REVIEW')}
        </div>
        <HelpButton topic="money" label={t.stillToCollect} />
      </div>
    </section>
  )
}
