import type { DICTS } from './i18n'

type Dict = (typeof DICTS)['en-IN']

const inrFmt = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 0 })

/** ₹ with Indian grouping; paise shown only when non-zero. */
export function inr(paise: number): string {
  const sign = paise < 0 ? '-' : ''
  const abs = Math.abs(paise)
  const rupees = abs / 100
  const s = abs % 100 === 0 ? inrFmt.format(Math.round(rupees)) : new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(rupees)
  return `${sign}₹${s}`
}

export function ago(iso: string | null, t: Dict): string {
  if (!iso) return t.never
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return t.justNow
  if (s < 3600) return `${Math.round(s / 60)} ${t.minAgo}`
  if (s < 86400) return `${Math.round(s / 3600)} ${t.hAgo}`
  return new Date(iso).toLocaleDateString()
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
