import { useCallback, useEffect, useState } from 'react'
import { Upload } from 'lucide-react'
import { api, ApiError, type Approval, type Decision, type Me, type Queue, type QueueChange } from './api'
import { usePrefs } from './prefs'
import { transition } from './motion'
import { ApproveDialog } from './components/ApproveDialog'
import { Assistant } from './components/Assistant'
import { CaseDrawer } from './components/CaseDrawer'
import { ChangeFeed } from './components/ChangeFeed'
import { HelpButton } from './components/HelpButton'
import { ImportDialog } from './components/ImportDialog'
import { Login } from './components/Login'
import { PeopleList } from './components/PeopleList'
import { Ledger } from './components/Summary'
import { TopBar } from './components/TopBar'
import { LivePanel } from './components/LivePanel'
import { ReconView } from './components/ReconView'

type Pending = { approval: Approval; name: string; reason: string }

export default function App() {
  const { t, lang } = usePrefs()
  const [me, setMe] = useState<Me | null | 'anon'>(null)
  const [q, setQ] = useState<Queue | null>(null)
  const [filter, setFilter] = useState<Decision | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [approve, setApprove] = useState<Pending | null>(null)
  const [importing, setImporting] = useState(false)
  const [liveOpen, setLiveOpen] = useState(false)
  const [view, setView] = useState<'queue' | 'recon'>('queue')
  const [feed, setFeed] = useState<QueueChange[]>([])
  const [flash, setFlash] = useState<Set<string>>(new Set())

  /** Signed-in user, or (on this machine, demo mode) open straight into the loan-book scenario. */
  const loadMe = useCallback(async () => {
    try {
      setMe(await api.me())
      return
    } catch {
      /* not signed in */
    }
    try {
      if ((await api.demo()).enabled) {
        await api.demoSession('meena')
        setMe(await api.me())
        return
      }
    } catch {
      /* fall through to sign-in */
    }
    setMe('anon')
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
      if (e instanceof ApiError && e.status === 401) loadMe()
    }
  }, [lang, loadMe])

  useEffect(() => {
    loadMe()
  }, [loadMe])

  useEffect(() => {
    if (!me || me === 'anon') return
    refresh()
    api
      .changes()
      .then((rows) => setFeed(rows.filter((r) => r.kind === 'queue_change' && r.result).map((r) => r.result as unknown as QueueChange)))
      .catch(() => {})
    const timer = window.setInterval(refresh, 4000)
    return () => window.clearInterval(timer)
  }, [me, refresh])

  async function switchTo(persona: string) {
    if (me && me !== 'anon' && me.operator.username === persona) return
    await api.demoSession(persona)
    const [nextMe, nextQ] = await Promise.all([api.me(), api.queue(lang)])
    transition(() => {
      setOpen(null)
      setFilter(null)
      setFeed([])
      setQ(nextQ)
      setMe(nextMe)
    })
  }

  if (me === null) return null
  if (me === 'anon') return <Login onDone={loadMe} />

  const openApproval = (approval: Approval, name: string, reason?: string) =>
    setApprove({ approval, name, reason: reason ?? q?.rows.find((r) => r.account_id === approval.account_id)?.reason ?? '' })

  return (
    <>
      <TopBar
        me={me}
        onSwitch={switchTo}
        onLive={() => setLiveOpen(true)}
        view={view}
        onView={(v) => transition(() => setView(v))}
        onSignOut={async () => {
          await api.logout().catch(() => {})
          setMe('anon')
          setQ(null)
        }}
      />
      <main className="page">
        {view === 'recon' ? (
          <ReconView />
        ) : (
        <>
        <div className="page-head">
          <div>
            <div className="overline">{new Date().toLocaleDateString(lang, { weekday: 'long', day: 'numeric', month: 'long' })}</div>
            <h1>{t.today}</h1>
          </div>
          <div className="head-actions">
            <button className="btn secondary" onClick={() => setImporting(true)}>
              <Upload size={15} strokeWidth={1.9} /> {t.addList}
            </button>
            <HelpButton topic="add_list" label={t.addList} />
          </div>
        </div>

        {q && <Ledger q={q} />}

        <div className="main">
          <PeopleList q={q} filter={filter} setFilter={(f) => transition(() => setFilter(f))} flash={flash} onOpen={setOpen} />
          <aside className="side">
            <Assistant
              voiceOn={me.integrations.sarvam.mode === 'live'}
              focusId={open}
              onCase={setOpen}
              onApprove={(a, name) => openApproval(a, name)}
              onChanged={refresh}
            />
            <ChangeFeed items={feed} onOpen={setOpen} />
          </aside>
        </div>
        </>
        )}
      </main>

      {liveOpen && (
        <LivePanel
          q={q}
          onClose={() => setLiveOpen(false)}
          onApprove={openApproval}
          onChanged={refresh}
          onOpen={(id) => {
            setLiveOpen(false)
            setOpen(id)
          }}
        />
      )}
      {open && <CaseDrawer id={open} onClose={() => setOpen(null)} onChanged={refresh} onApprove={openApproval} />}
      {approve && (
        <ApproveDialog
          approval={approve.approval}
          name={approve.name}
          reason={approve.reason}
          openCheckout={me.integrations.cashfree.mode === 'cashfree_sandbox'}
          onClose={() => setApprove(null)}
          onDone={refresh}
        />
      )}
      {importing && <ImportDialog onClose={() => setImporting(false)} onDone={refresh} />}
    </>
  )
}
