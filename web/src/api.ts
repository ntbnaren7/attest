// Thin client for the receivables backend. All money is integer paise.

export type Decision = 'CONTACT' | 'HOLD' | 'REVIEW' | 'CLOSED'
export type Lang = 'en-IN' | 'hi-IN' | 'ta-IN' | 'kn-IN'

export interface LinkInfo {
  link_id: string
  link_url: string | null
  status: string
  amount_paise: number
  amount_paid_paise: number
  provenance: string
}

export interface QueueRow {
  account_id: string
  external_ref: string
  display_name: string | null
  language: string | null
  rank: number
  decision: Decision
  code: string
  reason: string
  reason_local: string
  outstanding_paise: number
  collectible_paise: number
  disputed_paise: number
  score: number
  factors: string[]
  contact_allowed_now: boolean
  next_actions: string[]
  route_to: string | null
  offered_options: string[]
  verification_stale: boolean
  last_verified_at: string | null
  link: LinkInfo | null
  pending_approval: { id: string; amount_paise: number; status: string } | null
}

export interface QueueChange {
  account_id: string
  external_ref: string
  from: string
  to: string
  from_amount: string
  to_amount: string
  why: string
  at: string
}

export interface Queue {
  rows: QueueRow[]
  counts: Record<Decision, number>
  totals: { outstanding_paise: number; collectible_now_paise: number; received_paise: number; on_hold_paise: number }
  changes: QueueChange[]
  computed_at: string
}

export interface Integration { mode: string; label: string }
export interface Me {
  operator: { id: string; username: string; display_name: string; can_approve: boolean; mode: 'borrower' | 'merchant'; merchant_name: string }
  integrations: { cashfree: Integration; sarvam: Integration; relay: Integration }
  demo?: boolean
  personas?: Record<string, { label: string; detail: string }>
}

export interface Approval {
  id: string
  account_id: string
  action: string
  amount_paise: number
  account_version: number
  recipient: string
  status: string
  requested_by: string
  requested_via: string
}

export interface TraceRow {
  id: number
  kind: string
  actor: string
  target: string | null
  allowed_action: string | null
  input: Record<string, unknown> | null
  result: Record<string, unknown> | null
  ts: string
}

export interface CaseDetail {
  account: { id: string; external_ref: string; display_name: string | null; phone: string | null; language: string | null; version: number; provider_checked_at: string | null }
  decision: QueueRow
  dues: { id: string; external_ref: string; amount_paise: number; due_date: string | null }[]
  credits: { amount_paise: number; reference: string }[]
  claims: { id: string; kind: string; amount_paise: number | null; reference: string | null; promised_date: string | null; status: string; status_reason: string | null; source: string; text: string | null; created_at: string }[]
  batch_files: Record<string, string>
  receipts: { provider_payment_id: string; amount_paise: number; payment_status: string; provenance: string; verified_at: string }[]
  allocations: { due_id: string; amount_paise: number; basis: string }[]
  links: LinkInfo[]
  approvals: Approval[]
  outbox: { action: string; channel: string; status: string; created_at: string }[]
  trace: TraceRow[]
}

export interface Turn {
  conversation_id: string
  transcript: string
  via: 'voice' | 'text'
  reply: string
  reply_language: Lang
  reply_audio_b64?: string
  extraction: { source: string; intent: string; amount_paise: number | null; reference: string }
  handoffs: { to: string; action: string; status: string; summary: string }[]
  denials: { code: string; reason: string }[]
  account: { id: string; external_ref: string } | null
  approval_request?: Approval
}

export interface ImportReport {
  ok: boolean
  error?: string
  batch_id: string
  total_rows: number
  valid_rows: number
  invalid_rows: number
  invalid: { row: number; account_ref: string | null; errors: string[] }[]
  protected_columns_dropped: string[]
  held_out_columns: string[]
  amount_changes: { invoice: string; from: string; to: string }[]
}

export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) }
  if (init.body && !(init.body instanceof FormData)) headers['content-type'] = 'application/json'
  const res = await fetch(path, { credentials: 'same-origin', ...init, headers })
  const text = await res.text()
  let body: Record<string, unknown> = {}
  try {
    body = text ? JSON.parse(text) : {}
  } catch {
    body = { detail: text }
  }
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : typeof body.error === 'string' ? body.error : res.statusText
    throw new ApiError(res.status, detail, typeof body.error === 'string' ? body.error : undefined)
  }
  return body as T
}

const SCREEN = { 'x-approval-screen': '1' }

export const api = {
  login: (username: string, password: string) => call('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => call('/api/logout', { method: 'POST' }),
  demo: () => call<{ enabled: boolean }>('/api/demo'),
  demoSession: (persona: string) => call('/api/demo/session', { method: 'POST', body: JSON.stringify({ persona }) }),
  me: () => call<Me>('/api/me'),
  queue: (lang: Lang) => call<Queue>(`/api/queue?lang=${lang}`),
  caseDetail: (id: string) => call<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  explain: (id: string, lang: Lang) => call<{ text: string; audio_b64: string | null }>(`/api/cases/${encodeURIComponent(id)}/explain?lang=${lang}`),
  refresh: (id: string) => call(`/api/cases/${encodeURIComponent(id)}/refresh`, { method: 'POST' }),
  requestLink: (id: string) => call<{ status: string; summary: string; facts: { approval?: Approval } }>(`/api/cases/${encodeURIComponent(id)}/request-link`, { method: 'POST' }),
  approve: (a: Approval) =>
    call<{ token: string }>(`/api/approvals/${a.id}/approve`, {
      method: 'POST',
      headers: SCREEN,
      body: JSON.stringify({ confirm: { account_id: a.account_id, action: a.action, amount_paise: a.amount_paise, account_version: a.account_version, recipient: a.recipient } }),
    }),
  execute: (id: string, token: string) => call(`/api/approvals/${id}/execute`, { method: 'POST', headers: SCREEN, body: JSON.stringify({ token }) }),
  simulatePay: (link_id: string, amount_inr?: string) => call('/api/sim/pay', { method: 'POST', body: JSON.stringify({ link_id, amount_inr }) }),
  importFile: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return call<ImportReport>('/api/import', { method: 'POST', body: fd })
  },
  say: (text: string, language: Lang | null, conversation_id: string | null, account_id: string | null) =>
    call<Turn>('/api/coordinator/text', { method: 'POST', body: JSON.stringify({ text, language, conversation_id, account_id }) }),
  speak: (audio: Blob, language: Lang | null, conversation_id: string | null, account_id: string | null) => {
    const fd = new FormData()
    fd.append('audio', audio, audio.type.includes('mp4') ? 'speech.m4a' : 'speech.webm')
    fd.append('language', language ?? 'unknown')
    if (conversation_id) fd.append('conversation_id', conversation_id)
    if (account_id) fd.append('account_id', account_id)
    return call<Turn>('/api/coordinator/voice', { method: 'POST', body: fd })
  },
  changes: () => call<TraceRow[]>('/api/trace'),
  help: (topic: string, lang: Lang) => call<{ text: string; audio_b64: string | null }>(`/api/help/${encodeURIComponent(topic)}?lang=${lang}`),
}
