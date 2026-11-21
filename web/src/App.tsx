import { useCallback, useEffect, useState } from 'react'
import { Upload } from 'lucide-react'
import { api, ApiError, type Approval, type Decision, type Me, type Queue, type QueueChange } from './api'
import { usePrefs } from './prefs'
import { ApproveDialog } from './components/ApproveDialog'
import { Assistant } from './components/Assistant'
import { CaseDrawer } from './components/CaseDrawer'
import { ChangeFeed } from './components/ChangeFeed'
import { Login } from './components/Login'
import { PeopleList } from './components/PeopleList'
import { Summary } from './components/Summary'
import { TopBar } from './components/TopBar'
import { ImportDialog } from './components/ImportDialog'
import { HelpButton } from './components/HelpButton'

type Pending = { approval: Approval; name: string; reason: string }

export default function App() {
  const { t, lang } = usePrefs()
  const [me, setMe] = useState<Me | null | 'anon'>(null)
  const [q, setQ] = useState<Queue | null>(null)
  const [filter, setFilter] = useState<Decision | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [approve, setApprove] = useState<Pending | null>(null)
  const [importing, setImporting] = useState(false)
  const [feed, setFeed] = useState<QueueChange[]>([])
  const [flash, setFlash] = useState<Set<string>>(new Set())

  const loadMe = useCallback(async () => {
    try {
      setMe(await api.me())
    } catch {
      setMe('anon')
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const next = await api.queue(lang)
      setQ(next)
      if (next.changes.length) {
        setFeed((f) => [...next.changes, ...f].slice(0, 30))
        setFlash(new Set(next.changes.map((c) => c.account_id)))
        window.setTimeout(() => setFlash(new Set()), 2600)
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe('anon')
    }
  }, [lang])

  useEffect(() => {
    loadMe()
  }, [loadMe])

  useEffect(() => {
    if (!me || me === 'anon') return
    refresh()
    api
      .changes()
      .then((rows) =>
        setFeed(
          rows
            .filter((r) => r.kind === 'queue_change' && r.result)
            .map((r) => r.result as unknown as QueueChange),
        ),
      )
      .catch(() => {})
    const timer = window.setInterval(refresh, 4000)
    return () => window.clearInterval(timer)
  }, [me, refresh])

  if (me === null) return null
  if (me === 'anon') return <Login onDone={loadMe} />

  const openApproval = (approval: Approval, name: string, reason?: string) =>
    setApprove({ approval, name, reason: reason ?? q?.rows.find((r) => r.account_id === approval.account_id)?.reason ?? '' })

  return (
    <>
      <TopBar
        me={me}
        onSignOut={async () => {
          await api.logout().catch(() => {})
          setMe('anon')
          setQ(null)
        }}
      />
      <main className="shell">
        <div className="page-head">
          <div>
            <h1>{filter ? t[`group_${filter}`] : t.today}</h1>
            <div className="muted">
              {filter ? t[`groupHint_${filter}`] : new Date().toLocaleDateString(lang, { weekday: 'long', day: 'numeric', month: 'long' })}
            </div>
          </div>
          <div className="with-help">
            <button className="btn ghost" onClick={() => setImporting(true)}>
              <Upload size={16} /> {t.addList}
            </button>
            <HelpButton topic="add_list" label={t.addList} />
          </div>
        </div>

        {q && <Summary q={q} filter={filter} setFilter={setFilter} />}

        <div className="main">
          <PeopleList rows={q?.rows ?? []} filter={filter} flash={flash} onOpen={setOpen} onClear={() => setFilter(null)} />
          <div className="side">
            <Assistant
              voiceOn={me.integrations.sarvam.mode === 'live'}
              focusId={open}
              onCase={setOpen}
              onApprove={(a, name) => openApproval(a, name)}
              onChanged={refresh}
            />
            <ChangeFeed items={feed} onOpen={setOpen} />
          </div>
        </div>
      </main>

      {open && <CaseDrawer id={open} onClose={() => setOpen(null)} onChanged={refresh} onApprove={openApproval} />}
      {approve && (
        <ApproveDialog
          approval={approve.approval}
          name={approve.name}
          reason={approve.reason}
          onClose={() => setApprove(null)}
          onDone={refresh}
        />
      )}
      {importing && <ImportDialog onClose={() => setImporting(false)} onDone={refresh} />}
    </>
  )
}
