/**
 * @file normalizeEmail — transforms raw backend email responses into a stable UI model.
 *
 * The backend can return either `{ mail: {...} }` or `{ email: {...} }` shapes, and the
 * analysis cache may or may not be present. These functions smooth over all variants so the
 * rest of the frontend always deals with a consistent object shape.
 *
 * Key exports:
 *   normalizeEmailListItem(raw) — used by InboxPage for list rows
 *   normalizeEmailDetail(raw)   — used by EmailDetailPage; superset of the list shape
 */

function pickFirst(obj, keys) {
  for (const k of keys) {
    if (obj && obj[k] != null) return obj[k]
  }
  return undefined
}

function asString(v) {
  if (v == null) return ''
  if (typeof v === 'string') return v
  return String(v)
}

function asStringArray(v) {
  if (Array.isArray(v)) return v.map(asString).filter(Boolean)
  if (typeof v === 'string') return v.split(',').map((s) => s.trim()).filter(Boolean)
  return []
}

function normalizeSender(senderRaw, mail) {
  if (typeof senderRaw === 'string') return senderRaw
  if (senderRaw && typeof senderRaw === 'object') {
    return asString(pickFirst(senderRaw, ['display_name', 'name', 'email']))
  }
  return asString(pickFirst(mail, ['sender', 'from', 'headers_from']))
}

// function normalizeAnalysis(raw) {
//   const a = raw && typeof raw === 'object' ? raw : {}
//   const tags = asStringArray(pickFirst(a, ['suggested_tags', 'tags', 'tag', 'labels', 'categories']))
//   const priority =
//     asString(pickFirst(a, ['priority', 'urgency', 'importance', 'suggested_action'])).toLowerCase()
//   const summary = asString(pickFirst(a, ['analysis', 'summary', 'thread_summary', 'ai_summary']))
//   const briefSummary = asString(pickFirst(a, ['brief_analysis', 'brief_summary', 'brief_thread_summary', 'brief_ai_summary']))
//     const actionsRaw = pickFirst(a, ['actions', 'tasks', 'action_items', 'todos'])
//   const actions = Array.isArray(actionsRaw)
//     ? actionsRaw
//           .map((x) => {
//               if (typeof x === 'string') return { text: x, due: '' }
//               if (x && typeof x === 'object') {
//                   const rawDue = asString(pickFirst(x, ['due', 'deadline', 'date']))
//                   // Odcinamy godzinę (część po 'T'), żeby frontend dostał zgrabne 'YYYY-MM-DD'
//                   const formattedDue = rawDue && rawDue.includes('T') ? rawDue.split('T')[0] : rawDue
//                   return {
//                       text: asString(pickFirst(x, ['text', 'task', 'title'])),
//                       due: formattedDue
//                   }
//               }
//               return null
//           })
//         .filter(Boolean)
//     : []
//   return { tags, priority, summary, briefSummary, actions, raw: a }
// }

export function normalizeEmailListItem(raw) {
  const e = raw && typeof raw === 'object' ? raw : {}

  // Backend currently returns items like:
  // { mail: {...}, analysis: {...} } or { email: {...}, analysis: {...}, sender: {...} }
  const mail = (e.mail && typeof e.mail === 'object' ? e.mail : null) || (e.email && typeof e.email === 'object' ? e.email : null) || e
  const analysisRaw = e.analysis && typeof e.analysis === 'object' ? e.analysis : {}

  const id = asString(pickFirst(mail, ['id', 'email_id', 'message_id', 'mail_id']))
  const threadId = asString(pickFirst(mail, ['thread_id', 'threadId']))
  const senderId = asString(pickFirst(mail, ['sender_id', 'senderId']))
  const subject = asString(pickFirst(mail, ['subject', 'headers_subject']))
  const from = normalizeSender(e.sender, mail)
  const date = asString(pickFirst(mail, ['date', 'internal_date', 'headers_date', 'received_at', 'created_at']))
  const snippet = asString(pickFirst(mail, ['snippet', 'preview', 'body']))

  const labels = asStringArray(pickFirst(mail, ['labels']))

  // Map backend analysis fields into the UI model.
  // We don't yet have explicit "tags/priority/actions" fields in the sample response,
  // so we derive a useful approximation for the coordinator UI.
  const derivedTags = asStringArray(pickFirst(analysisRaw, ['suggested_tags', 'tags', 'tag', 'labels', 'categories']))
  const category = asString(analysisRaw.category || '')
  if (category) derivedTags.push(category)
  const sentiment = asString(analysisRaw.sentiment || '')
  if (sentiment) derivedTags.push(sentiment)

  const importanceScore = analysisRaw.importance_score != null ? Number(analysisRaw.importance_score) : NaN
  const confidenceScore = Number(analysisRaw.confidence_score)
  const priority = asString(pickFirst(analysisRaw, ['priority', 'urgency', 'importance'])).toLowerCase() ||
    (analysisRaw.key_action_required === true || labels.includes('IMPORTANT')
      ? 'high'
      : Number.isFinite(importanceScore) && importanceScore >= 4
        ? 'high'
        : Number.isFinite(importanceScore) && importanceScore >= 2
          ? 'medium'
          : asString(analysisRaw.suggested_action).toLowerCase() === 'escalate'
            ? 'high'
            : Number.isFinite(confidenceScore) && confidenceScore >= 0.75
              ? 'medium'
              : 'low')

  const actions = analysisRaw.actions

  const briefSummary = asString(pickFirst(analysisRaw, ['summary']))
  const summary = asString(pickFirst(analysisRaw, ['analysis']))

  const analysis = {
    tags: derivedTags,
    priority,
    summary,
    briefSummary,
    actions,
    suggestedReply: asString(pickFirst(analysisRaw, ['suggested_reply', 'reply_draft'])),
    suggestedAction: asString(analysisRaw.suggested_action).toLowerCase(),
    confidenceScore: Number.isFinite(confidenceScore) ? confidenceScore : null,
    importanceScore: Number.isFinite(importanceScore) ? importanceScore : null,
    modelUsed: asString(analysisRaw.model_used),
    raw: analysisRaw,
  }
    console.log(analysis)
  return { id, threadId, senderId, subject, from, date, snippet, analysis, raw: e }
}

export function normalizeEmailDetail(raw) {
  const e = raw && typeof raw === 'object' ? raw : {}
  const detailMail = e.mail && typeof e.mail === 'object' ? e.mail : null
  const detailEmail = e.email && typeof e.email === 'object' ? e.email : null
  const cachedAnalysis = e.analysis && typeof e.analysis === 'object' ? e.analysis : null
  const mail = detailMail || detailEmail || (e.email && typeof e.email === 'object' ? e.email : null) || e
  const body =
    asString(pickFirst(mail, ['body_plain', 'plain', 'body', 'full_body', 'text', 'content', 'message_body'])) ||
    asString(pickFirst(mail, ['body_html', 'html']))

  const base = normalizeEmailListItem({
    ...(detailMail ? { mail: detailMail } : {}),
    ...(detailEmail ? { email: detailEmail } : {}),
    ...(cachedAnalysis ? { analysis: cachedAnalysis } : {}),
    ...(e.sender && typeof e.sender === 'object' ? { sender: e.sender } : {}),
  })

  const threadSummary = e.thread_summary && typeof e.thread_summary === 'string' ? e.thread_summary : null

  return {
    ...base,
    body,
    threadSummary,
    processingTimeMs: Number(e.processing_time_ms) || 0,
    raw: e,
  }
}

