import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import type { Lang } from './api'
import { DICTS } from './i18n'
import { transition } from './motion'

type Theme = 'light' | 'dark'

// Per-viewer conveniences only; the page works if storage is unavailable.
function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}
function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

interface Prefs {
  lang: Lang
  setLang: (l: Lang) => void
  theme: Theme
  toggleTheme: () => void
  t: (typeof DICTS)['en-IN']
}

const Ctx = createContext<Prefs | null>(null)

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => (read('ui.lang') as Lang) || 'en-IN')
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = read('ui.theme') as Theme | null
    if (saved) return saved
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  // Layout effect so the new theme is painted inside the same view-transition snapshot.
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  useEffect(() => {
    document.documentElement.lang = lang.slice(0, 2)
  }, [lang])
  const value: Prefs = {
    lang,
    setLang: (l) => {
      transition(() => setLangState(l))
      write('ui.lang', l)
    },
    theme,
    toggleTheme: () =>
      transition(() =>
        setTheme((x) => {
          const n = x === 'light' ? 'dark' : 'light'
          write('ui.theme', n)
          return n
        }),
      ),
    t: DICTS[lang],
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function usePrefs(): Prefs {
  const v = useContext(Ctx)
  if (!v) throw new Error('usePrefs outside PrefsProvider')
  return v
}
