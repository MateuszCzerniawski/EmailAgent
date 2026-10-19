/**
 * @file client.js — all HTTP calls to the backend API.
 *
 * Every function in this file is a thin wrapper around the shared `request()` helper, which
 * reads VITE_API_BASE_URL at call time (defaults to :8000; proxy sets it to :8001 in dev).
 * JSDoc @typedef blocks at the top mirror the backend Pydantic models so callers get type hints.
 *
 * Base URL is resolved via VITE_API_BASE_URL env var (set by the Vite proxy config for dev).
 */

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

function getBaseUrl() {
  return (import.meta.env?.VITE_API_BASE_URL || DEFAULT_BASE_URL).replace(/\/$/, '')
}

/**
 * @typedef {{ authenticated: boolean }} AuthStatusResponse
 * @typedef {{ id?: string, name: string }} TagModel
 * @typedef {{ name: string }} TagCreateInput
 * @typedef {{ id?: string, filename: string, file_type: 'markdown' | 'pdf', category: string, version: number, content: string, storage_path: string, embedding_model?: string | null, embedded_at?: string | null, created_at?: string | null, updated_at?: string | null }} GuidelineModel
 * @typedef {{ file_path: string }} GuidelineCreateInput
 * @typedef {{ source: string, document: string, score: number, metadata: Record<string, unknown> }} SearchResult
 * @typedef {{ missing_in_chroma: string[], orphaned_in_chroma: string[], total_in_postgres: number, total_in_chroma: number, checked_at: string }} SyncReport
 * @typedef {{ emails_reembedded: number, guidelines_reembedded: number, errors: string[], started_at: string, completed_at: string }} RefreshReport
 * @typedef {'internal' | 'client' | 'vip' | 'unknown'} RelationType
 * @typedef {{ id?: string, email: string, display_name?: string | null, importance_score: number, relation_type: RelationType, mail_count: number, last_contact_at?: string | null, summary?: string | null, summary_updated_at?: string | null, created_at?: string | null }} SenderModel
 * @typedef {'open' | 'closed' | 'pending'} ThreadStatus
 * @typedef {{ id?: string, subject: string, sender_id: string, mail_count: number, started_at?: string | null, last_mail_at?: string | null, summary?: string | null, summary_updated_at?: string | null, status: ThreadStatus }} ThreadModel
 * @typedef {{ thread: ThreadModel, sender?: SenderModel | null, latest_email_id?: string | null }} ThreadWithSenderResponse
 * @typedef {'reply' | 'archive' | 'escalate' | 'ignore'} SuggestedAction
 * @typedef {{ id?: string, email_id: string, analysis: string, suggested_reply?: string | null, suggested_tags: string[], suggested_action: SuggestedAction, confidence_score?: number | null, model_used: string, created_at?: string | null, updated_at?: string | null }} AgentCacheModel
 * @typedef {{ mail: Record<string, unknown>, processing_time_ms: number }} RawEmailDetailResponse
 * @typedef {{ email: Record<string, unknown>, analysis: AgentCacheModel, processing_time_ms: number }} AnalyseEmailResponse
 */

async function request(path, { method = 'GET', body: requestBody, headers, signal } = {}) {
  const url = `${getBaseUrl()}${path}`
  const res = await fetch(url, {
    method,
    signal,
    headers: {
      ...(requestBody != null ? { 'Content-Type': 'application/json' } : {}),
      ...(headers || {}),
    },
    ...(requestBody != null ? { body: JSON.stringify(requestBody) } : {}),
  })
  const contentType = res.headers.get('content-type') || ''
  const looksJson = contentType.includes('application/json') || contentType.includes('+json')

  const text = await res.text().catch(() => '')
  let body = null
  if (looksJson || text.trim().startsWith('{') || text.trim().startsWith('[')) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  } else {
    body = text
  }

  if (!res.ok) {
    const message =
      (body && typeof body === 'object' && (body.detail || body.message)) ||
      (typeof body === 'string' && body) ||
      `Request failed (${res.status})`
    const err = new Error(message)
    err.status = res.status
    err.body = body
    throw err
  }

  return body
}

/**
 * @param {{ maxResults?: number|string, query?: string, signal?: AbortSignal }} [options]
 * @returns {Promise<Array<Record<string, unknown>>>}
 */
export async function listEmails({ maxResults, query, signal } = {}) {
  const params = new URLSearchParams()
  if (maxResults != null && maxResults !== '') params.set('max_results', String(maxResults))
  if (query) params.set('query', query)
  const qs = params.toString()
  return await request(`/emails${qs ? `?${qs}` : ''}`, { signal })
}

/**
 * @param {string} emailId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<Record<string, unknown>>}
 */
export async function getEmail(emailId, { signal } = {}) {
  return await request(`/emails/${encodeURIComponent(emailId)}`, { signal })
}

/**
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<Array<Record<string, unknown>>>}
 */
export async function testEmailsEndpoint({ signal } = {}) {
  return await request(`/emails?max_results=2`, { signal })
}

/**
 * @param {{ limit?: number, offset?: number, signal?: AbortSignal }} [options]
 * @returns {Promise<SenderModel[]>}
 */
export async function listSenders({ limit, offset, signal } = {}) {
  const params = new URLSearchParams()
  if (limit != null) params.set('limit', String(limit))
  if (offset != null) params.set('offset', String(offset))
  const qs = params.toString()
  return await request(`/emails/senders${qs ? `?${qs}` : ''}`, { signal })
}

/**
 * @param {string} senderId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<SenderModel>}
 */
export async function getSender(senderId, { signal } = {}) {
  return await request(`/emails/senders/${encodeURIComponent(senderId)}`, { signal })
}

/**
 * @param {{ topK?: number, senderId?: string, signal?: AbortSignal }} [options]
 * @returns {Promise<ThreadWithSenderResponse[]>}
 */
export async function listThreads({ topK, senderId, signal } = {}) {
  const params = new URLSearchParams()
  if (topK != null) params.set('top_k', String(topK))
  if (senderId != null) params.set('sender_id', String(senderId))
  const qs = params.toString()
  return await request(`/emails/threads${qs ? `?${qs}` : ''}`, { signal })
}

/**
 * @param {string} threadId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<EmailInThreadItem[]>}
 */
export async function listEmailsInThread(threadId, { signal } = {}) {
  return await request(`/emails/threads/${encodeURIComponent(threadId)}/emails`, { signal })
}

/**
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<AuthStatusResponse>}
 */
export async function getAuthStatus({ signal } = {}) {
  return await request('/auth/status', { signal })
}

/**
 * @param {TagCreateInput} input
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<TagModel>}
 */
export async function createTag(input, { signal } = {}) {
  return await request('/database/tags', { method: 'POST', body: input, signal })
}

/**
 * @param {GuidelineCreateInput} input
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<GuidelineModel>}
 */
export async function createGuideline(input, { signal } = {}) {
  return await request('/database/guidelines', { method: 'POST', body: input, signal })
}

/**
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<SyncReport>}
 */
export async function getVectorSyncReport({ signal } = {}) {
  return await request('/database/sync/check', { signal })
}

/**
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<RefreshReport>}
 */
export async function refreshVectorDatabase({ signal } = {}) {
  return await request('/database/sync/refresh', { method: 'POST', signal })
}

/**
 * @param {{ query: string, topK?: number, signal?: AbortSignal }} options
 * @returns {Promise<SearchResult[]>}
 */
export async function searchKnowledgeBase({ query, topK, signal } = {}) {
  const params = new URLSearchParams()
  if (query) params.set('q', query)
  if (topK != null) params.set('top_k', String(topK))
  const qs = params.toString()
  return await request(`/database/search${qs ? `?${qs}` : ''}`, { signal })
}

/**
 * @param {string} emailId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<RawEmailDetailResponse>}
 */
export async function getRawEmail(emailId, { signal } = {}) {
  return await request(`/emails/raw/${encodeURIComponent(emailId)}`, { signal })
}

/**
 * @param {string} emailId
 * @param {{ forceRegeneration?: boolean, signal?: AbortSignal }} [options]
 * @returns {Promise<AnalyseEmailResponse>}
 */
export async function analyseEmail(emailId, { forceRegeneration, signal } = {}) {
  const params = new URLSearchParams()
  if (forceRegeneration != null) params.set('force_regeneration', String(Boolean(forceRegeneration)))
  const qs = params.toString()
  return await request(`/emails/analyse/${encodeURIComponent(emailId)}${qs ? `?${qs}` : ''}`, { signal })
}

/**
 * @param {string} emailId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<AgentCacheModel>}
 */
export async function getEmailCache(emailId, { signal } = {}) {
  return await request(`/database/emails/${encodeURIComponent(emailId)}/cache`, { signal })
}

/**
 * @param {string} threadId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ thread_id: string, summary: string, cached: boolean, message_count: number }>}
 */
export async function generateThreadSummary(threadId, { signal } = {}) {
  return await request(
    `/emails/threads/${encodeURIComponent(threadId)}/summary`,
    { method: 'POST', signal }
  )
}

/**
 * @param {string} threadId
 * @param {string} summary
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ thread_id: string, summary: string, cached: boolean, message_count: number }>}
 */
export async function updateThreadSummary(threadId, summary, { signal } = {}) {
  return await request(
    `/emails/threads/${encodeURIComponent(threadId)}/summary`,
    { method: 'PUT', body: { summary }, signal }
  )
}

/**
 * @param {string} senderId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ sender_id: string, summary: string, cached: boolean }>}
 */
export async function generateSenderSummary(senderId, { signal } = {}) {
  return await request(
    `/emails/senders/${encodeURIComponent(senderId)}/summary`,
    { method: 'POST', signal }
  )
}

/**
 * @param {string} senderId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ tags: string[] }>}
 */
export async function getSenderTags(senderId, { signal } = {}) {
  return await request(`/emails/senders/${encodeURIComponent(senderId)}/tags`, { signal })
}

/**
 * @param {string} emailId
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ email_id: string, suggested_reply: string }>}
 */
/**
 * @param {string} emailId
 * @param {string[]} tags
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ email_id: string, tags: string[] }>}
 */
export async function updateEmailMetadata(emailId, { tags, importance }, { signal } = {}) {
  const body = { tags }
  if (importance != null) body.importance = importance
  return await request(`/emails/${encodeURIComponent(emailId)}/metadata`, { method: 'PUT', body, signal })
}

export async function generateSuggestedReply(emailId, { signal } = {}) {
  return await request(`/emails/${encodeURIComponent(emailId)}/suggested-reply`, { method: 'POST', signal })
}

/**
 * @param {Blob} audioBlob
 * @param {{ lang?: string, signal?: AbortSignal }} [options]
 * @returns {Promise<{ text: string }>}
 */
export async function transcribeAudio(audioBlob, { lang = 'pl', signal } = {}) {
  const formData = new FormData()
  const ext = (audioBlob.type || '').includes('ogg') ? 'ogg' : 'webm'
  formData.append('audio', audioBlob, `recording.${ext}`)
  formData.append('lang', lang.slice(0, 2))
  const url = `${getBaseUrl()}/stt/transcribe`
  const res = await fetch(url, { method: 'POST', body: formData, signal })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(body.detail || `STT failed (${res.status})`)
    err.status = res.status
    throw err
  }
  return res.json()
}

/**
 * @param {string} emailId
 * @param {string} reply
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ email_id: string, suggested_reply: string }>}
 */
export async function updateSuggestedReply(emailId, reply, { signal } = {}) {
  return await request(`/emails/${encodeURIComponent(emailId)}/suggested-reply`, { method: 'PUT', body: { reply }, signal })
}

/**
 * @param {{ to: string, subject?: string, body: string, thread_id?: string | null }} payload
 * @returns {Promise<{ message_id: string, to: string, subject: string }>}
 */
export async function sendEmail(payload) {
  return await request('/emails/send', { method: 'POST', body: payload })
}

/**
 * @param {SyncReport | null | undefined} report
 * @returns {SyncReport & { healthy: boolean, missingCount: number, orphanedCount: number }}
 */
/**
 * @returns {Promise<{ provider: string, openai_model: string, ollama_model: string, ollama_base_url: string }>}
 */
export async function getLlmConfig({ signal } = {}) {
  return await request('/config/llm', { signal })
}

/**
 * @param {{ provider?: string, openai_model?: string, ollama_model?: string, ollama_base_url?: string }} body
 * @returns {Promise<{ ok: boolean }>}
 */
export async function updateLlmConfig(body, { signal } = {}) {
  return await request('/config/llm', { method: 'PUT', body, signal })
}

/**
 * @returns {Promise<string>}
 */
export async function getOllamaGuide({ signal } = {}) {
  return await request('/config/ollama-guide', { signal })
}

/**
 * @returns {Promise<{ running: boolean, models: string[] }>}
 */
export async function getOllamaModels({ signal } = {}) {
  return await request('/config/ollama/models', { signal })
}

/**
 * @param {{ message: string, history: Array<{role:string,content:string}>, topK?: number }} payload
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{ reply: string, citations: Array<{subject:string,sender:string,received_at:string,snippet:string,score:number}> }>}
 */
export async function sendChatMessage({ message, history, topK = 5 }, { signal } = {}) {
  return await request('/chat/message', {
    method: 'POST',
    body: { message, history, top_k: topK },
    signal,
  })
}

export function summarizeSyncReport(report) {
  const safe = report && typeof report === 'object' ? report : {}
  const missing = Array.isArray(safe.missing_in_chroma) ? safe.missing_in_chroma : []
  const orphaned = Array.isArray(safe.orphaned_in_chroma) ? safe.orphaned_in_chroma : []
  return {
    missing_in_chroma: missing,
    orphaned_in_chroma: orphaned,
    total_in_postgres: Number(safe.total_in_postgres) || 0,
    total_in_chroma: Number(safe.total_in_chroma) || 0,
    checked_at: safe.checked_at || '',
    healthy: missing.length === 0 && orphaned.length === 0,
    missingCount: missing.length,
    orphanedCount: orphaned.length,
  }
}

/**
 * @param {RefreshReport | null | undefined} report
 * @returns {RefreshReport & { errorCount: number, totalReembedded: number }}
 */
export function summarizeRefreshReport(report) {
  const safe = report && typeof report === 'object' ? report : {}
  const errors = Array.isArray(safe.errors) ? safe.errors : []
  const emails = Number(safe.emails_reembedded) || 0
  const guidelines = Number(safe.guidelines_reembedded) || 0
  return {
    emails_reembedded: emails,
    guidelines_reembedded: guidelines,
    errors,
    started_at: safe.started_at || '',
    completed_at: safe.completed_at || '',
    errorCount: errors.length,
    totalReembedded: emails + guidelines,
  }
}

