import { ApiError } from './api'
import type { DICTS } from './i18n'

/** Turn API errors into short sentences in the operator's language where we can. */
export function errText(e: unknown, t: (typeof DICTS)['en-IN']): string {
  if (e instanceof ApiError) {
    if (e.status === 429 && /budget/i.test(e.message)) return t.budgetOut
    if (e.status === 429) return t.tooMany
    return e.message
  }
  return String(e)
}
