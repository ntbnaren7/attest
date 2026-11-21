import type { Decision, Queue } from '../api'
import { ago, inr } from '../format'
import { groupLabel, type Key } from '../i18n'
import { usePrefs } from '../prefs'
import { HelpButton } from './HelpButton'
import { Dot, GROUPS } from './Status'

export function PeopleList({ q, filter, setFilter, flash, onOpen }: {
  q: Queue | null
  filter: Decision | null
  setFilter: (d: Decision | null) => void
  flash: Set<string>
  onOpen: (id: string) => void
}) {
  const { t } = usePrefs()
  const rows = q?.rows ?? []
  const shown = filter ? rows.filter((r) => r.decision === filter) : rows
  const sum = shown.reduce((s, r) => s + (r.decision === 'CLOSED' ? 0 : r.outstanding_paise), 0)
  const tabs: (Decision | null)[] = [null, ...GROUPS]

  return (
    <section aria-label={t.all}>
      <div className="tabs" role="tablist">
        {tabs.map((g) => (
          <button key={g ?? 'all'} role="tab" className="tab" aria-selected={filter === g} onClick={() => setFilter(g)}>
            {g && <Dot d={g} />}
            {g ? groupLabel(t, g) : t.all}
            <span className="count">{g ? q?.counts[g] ?? 0 : rows.length}</span>
          </button>
        ))}
      </div>

      <div className="tab-note">
        <span>
          {filter && <>{t[`groupHint_${filter}` as Key]} · </>}
          {shown.length > 0 && (
            <>
              <span className="sum">{inr(sum)}</span> {t.across} {shown.length} {shown.length === 1 ? t.person : t.people}
            </>
          )}
        </span>
        {filter && <HelpButton topic={`group_${filter}`} label={groupLabel(t, filter)} />}
      </div>

      {shown.length === 0 && <div className="empty">{rows.length ? t.empty : t.emptyStart}</div>}
      <ul className="rows">
        {shown.map((r) => {
          const late = r.decision === 'CONTACT' && !r.contact_allowed_now
          return (
            <li key={r.account_id}>
              <button className={`row ${flash.has(r.account_id) ? 'flash' : ''}`} onClick={() => onOpen(r.account_id)}>
                <Dot d={r.decision} />
                <span style={{ minWidth: 0 }}>
                  <span className="who" style={{ display: 'block' }}>{r.display_name || r.external_ref}</span>
                  <span className="ref" style={{ display: 'block' }}>
                    {r.external_ref} · {groupLabel(t, r.decision)}
                  </span>
                </span>
                <span className="reason">{r.reason_local}</span>
                <span>
                  <span className={`amount ${r.decision === 'CLOSED' ? 'zero' : ''}`} style={{ display: 'block' }}>
                    {r.decision === 'CLOSED' ? inr(0) : inr(r.outstanding_paise)}
                  </span>
                  <span className={`when ${late ? 'warn' : ''}`} style={{ display: 'block' }}>
                    {late ? t.afterEight : `${t.checked} ${ago(r.last_verified_at, t)}`}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
