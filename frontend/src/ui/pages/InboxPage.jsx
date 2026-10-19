/**
 * @file InboxPage — primary inbox view.
 *
 * Renders a two-column layout: a SenderSidebar on the left (collapsible sender/thread tree)
 * and a paginated, searchable email list on the right. Each row shows sender, subject,
 * snippet, priority badge, importance score, and relation badge. Clicking a row navigates
 * to EmailDetailPage.
 *
 * Data flow: listEmailsPage() → normalizeEmailListItem() → local state → table rows.
 * Filters (search, priority, status) are applied client-side over the fetched page.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { listThreads, generateThreadSummary } from '../../api/client'
import { SenderInfoPanel } from '../components/SenderInfoPanel'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Alert } from '../components/Alert'
import { Card, CardHeader } from '../components/Card'
import { Input, Select, FormField } from '../components/Form'
import { SkeletonLine } from '../components/Skeleton'
import { SenderSidebar } from '../components/SenderSidebar'

function relationTone(relationType) {
  const relation = (relationType || '').toLowerCase()
  if (relation === 'vip') return 'amber'
  if (relation === 'internal') return 'blue'
  if (relation === 'client') return 'violet'
  return 'slate'
}

function statusTone(status) {
  const value = (status || '').toLowerCase()
  if (value === 'open') return 'green'
  if (value === 'pending') return 'amber'
  return 'slate'
}

function derivePriority(importanceScore) {
  const s = importanceScore || 0
  if (s >= 4) return { label: 'high', tone: 'rose' }
  if (s >= 3) return { label: 'medium', tone: 'amber' }
  if (s >= 1) return { label: 'low', tone: 'green' }
  return null
}

function formatDate(value) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString()
  } catch {
    return String(value)
  }
}

export function InboxPage() {
  const [query, setQuery] = React.useState('')
  const [topK, setTopK] = React.useState(12)
  const [relationFilter, setRelationFilter] = React.useState('')
  const [statusFilter, setStatusFilter] = React.useState('')
  const [sortMode, setSortMode] = React.useState('activity')
  const [selectedSenderId, setSelectedSenderId] = React.useState('')
  const [selectedSender, setSelectedSender] = React.useState(null)
  const [status, setStatus] = React.useState('idle')
  const [error, setError] = React.useState(null)
  const [threads, setThreads] = React.useState([])
  const [generatingSummaries, setGeneratingSummaries] = React.useState(new Set())

  const filtered = React.useMemo(() => {
    const base = threads.filter((item) => {
      const subject = item.thread?.subject || ''
      const senderName = item.sender?.display_name || item.sender?.email || ''
      const summary = item.thread?.summary || item.sender?.summary || ''
      const haystack = `${subject} ${senderName} ${summary}`.toLowerCase()
      if (query.trim() && !haystack.includes(query.trim().toLowerCase())) return false
      if (relationFilter && item.sender?.relation_type !== relationFilter) return false
      if (statusFilter && item.thread?.status !== statusFilter) return false
      return true
    })
    const sorted = [...base]
    if (sortMode === 'importance') {
      sorted.sort((a, b) => (b.sender?.importance_score || 0) - (a.sender?.importance_score || 0))
    } else {
      sorted.sort((a, b) => (b.thread?.last_mail_at || '').localeCompare(a.thread?.last_mail_at || ''))
    }
    return sorted
  }, [threads, query, relationFilter, statusFilter, sortMode])

  async function load({ signal } = {}) {
    setStatus('loading')
    setError(null)
    try {
      const raw = await listThreads({
        topK: Number(topK) || 12,
        senderId: selectedSenderId || undefined,
        signal,
      })
      setThreads(Array.isArray(raw) ? raw : [])
      setStatus('success')
    } catch (e) {
      if (e?.name === 'AbortError') return
      setError(e)
      setStatus('error')
    }
  }

  async function handleGenerateThreadSummary(e, threadId) {
    e.preventDefault()
    e.stopPropagation()
    if (!threadId || generatingSummaries.has(threadId)) return
    setGeneratingSummaries((prev) => new Set([...prev, threadId]))
    try {
      const result = await generateThreadSummary(threadId)
      setThreads((prev) =>
        prev.map((item) =>
          item.thread?.id === threadId
            ? { ...item, thread: { ...item.thread, summary: result.summary, summary_updated_at: new Date().toISOString() } }
            : item
        )
      )
    } catch (err) {
      console.error('Thread summary generation failed:', err)
    } finally {
      setGeneratingSummaries((prev) => {
        const next = new Set(prev)
        next.delete(threadId)
        return next
      })
    }
  }

  React.useEffect(() => {
    const controller = new AbortController()
    load({ signal: controller.signal })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    if (status === 'idle') return
    const controller = new AbortController()
    load({ signal: controller.signal })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSenderId])

  return (
    <div className="flex items-start gap-4">
      <div className="hidden lg:block w-72 flex-shrink-0">
        <SenderSidebar
          selectedSenderId={selectedSenderId}
          onThreadSelect={(item) => {
            setSelectedSenderId(item?.sender?.id || '')
            setSelectedSender(item?.sender || null)
          }}
        />
      </div>

      <div className="flex-1 min-w-0 space-y-4">
      <Card>
        <CardHeader
          title="Inbox threads"
          description="Grouped by sender and thread, with relation type and sender importance surfaced inline."
          actions={
            <Button
              variant="primary"
              onClick={() => load({})}
              disabled={status === 'loading'}
              title="Reload list"
            >
              {status === 'loading' ? 'Loading…' : 'Refresh'}
            </Button>
          }
        />

        <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
          <FormField label="Search threads" className="md:col-span-5">
            <Input
              placeholder="Subject, sender, or thread summary"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </FormField>

          <FormField label="Top threads" className="md:col-span-2">
            <Input
              type="number"
              min="1"
              max="500"
              value={topK}
              onChange={(e) => setTopK(e.target.value)}
            />
          </FormField>

          <FormField label="Relation type" className="md:col-span-2">
            <Select value={relationFilter} onChange={(e) => setRelationFilter(e.target.value)}>
              <option value="">All</option>
              <option value="vip">VIP</option>
              <option value="internal">Internal</option>
              <option value="client">Client</option>
              <option value="unknown">Unknown</option>
            </Select>
          </FormField>

          <FormField label="Thread status" className="md:col-span-3">
            <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All</option>
              <option value="open">Open</option>
              <option value="pending">Pending</option>
              <option value="closed">Closed</option>
            </Select>
          </FormField>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => load({})}
            disabled={status === 'loading'}
            title="Apply filters"
          >
            Apply
          </Button>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-xs font-medium text-slate-700">Sort</span>
            <Select
              className="h-9 w-auto"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value)}
            >
              <option value="activity">Latest activity</option>
              <option value="importance">Sender importance</option>
            </Select>
          </label>
          <Button
            variant="ghost"
            onClick={() => {
              setQuery('')
              setTopK(12)
              setRelationFilter('')
              setStatusFilter('')
              setSortMode('activity')
              setSelectedSenderId('')
              setSelectedSender(null)
            }}
          >
            Reset filters
          </Button>
          <div className="ml-auto text-xs text-slate-500">
            {status === 'success'
              ? `${filtered.length} shown (loaded ${threads.length})`
              : status === 'loading'
                ? 'Loading…'
                : ''}
          </div>
        </div>
      </Card>

      {selectedSenderId && selectedSender && (
        <SenderInfoPanel
          sender={selectedSender}
          threadCount={threads.length}
          onSummaryUpdated={(newSummary) =>
            setSelectedSender((prev) => prev ? { ...prev, summary: newSummary } : prev)
          }
        />
      )}

      {status === 'error' && (
        <Alert
          variant="error"
          title="Couldn't load emails"
          actions={
            <Button variant="secondary" onClick={() => load({})}>
              Try again
            </Button>
          }
        >
          {String(error?.message || error)}
        </Alert>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="divide-y divide-slate-100">
          {status === 'loading' && threads.length === 0
            ? Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="p-4">
                  <SkeletonLine height="h-4" width="w-1/2" />
                  <SkeletonLine height="h-3" width="w-2/3" className="mt-2" />
                  <div className="mt-3 flex gap-2">
                    <SkeletonLine height="h-5" width="w-16" className="rounded-full" />
                    <SkeletonLine height="h-5" width="w-20" className="rounded-full" />
                  </div>
                </div>
              ))
            : filtered.map((item) => {
                const senderName = item.sender?.display_name || item.sender?.email || '(Unknown sender)'
                const relationType = item.sender?.relation_type || 'unknown'
                const emailImportance = item.latest_email_importance ?? null
                const importance = emailImportance ?? item.sender?.importance_score ?? 0
                const priority = derivePriority(importance)
                const statusValue = item.thread?.status || 'open'
                const Wrapper = item.latest_email_id ? Link : 'div'
                return (
                  <Wrapper
                    key={item.thread?.id || item.latest_email_id}
                    {...(item.latest_email_id ? { to: `/emails/${encodeURIComponent(item.latest_email_id)}` } : {})}
                    className="block p-4 transition hover:bg-slate-50"
                  >
                    <div className="flex flex-col gap-1 md:flex-row md:items-baseline md:justify-between">
                      <div className="text-sm font-semibold text-slate-900">
                        {item.thread?.subject || '(No subject)'}
                      </div>
                      <div className="flex items-baseline gap-2">
                        <div className="text-xs text-slate-600">{senderName}</div>
                        {item.thread?.last_mail_at ? <div className="text-xs text-slate-400">{formatDate(item.thread.last_mail_at)}</div> : null}
                      </div>
                    </div>
                    <div className="mt-1 text-sm text-slate-700 flex items-start gap-2 min-h-[1.25rem]">
                      {item.thread?.summary ? (
                        <>
                          <span className="flex-1 line-clamp-2">{item.thread.summary}</span>
                          <button
                            type="button"
                            className="flex-shrink-0 text-slate-400 hover:text-slate-700 transition p-0.5 rounded disabled:opacity-50"
                            title="Refresh summary"
                            disabled={generatingSummaries.has(item.thread.id)}
                            onClick={(e) => handleGenerateThreadSummary(e, item.thread.id)}
                          >
                            {generatingSummaries.has(item.thread.id)
                              ? <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" /></svg>
                              : <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                            }
                          </button>
                        </>
                      ) : item.sender?.summary ? (
                        <span className="flex-1 line-clamp-2">{item.sender.summary}</span>
                      ) : (
                        <>
                          <span className="flex-1 text-slate-400">(No summary available)</span>
                          <button
                            type="button"
                            className="flex-shrink-0 text-xs font-medium text-blue-600 hover:text-blue-800 transition px-1.5 py-0.5 rounded border border-blue-200 hover:border-blue-400 disabled:opacity-50"
                            disabled={generatingSummaries.has(item.thread?.id)}
                            onClick={(e) => handleGenerateThreadSummary(e, item.thread?.id)}
                          >
                            {generatingSummaries.has(item.thread?.id) ? 'Generating…' : 'Generate'}
                          </button>
                        </>
                      )}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <Badge tone={statusTone(statusValue)}>{statusValue}</Badge>
                      <Badge tone={relationTone(relationType)}>{relationType}</Badge>
                      <Badge tone="slate">{item.thread?.mail_count || 0} emails</Badge>
                      <Badge tone="blue">importance {importance}/5</Badge>
                      {priority ? <Badge tone={priority.tone}>{priority.label} priority</Badge> : null}
                    </div>

                  </Wrapper>
                )
              })}

          {status !== 'loading' && filtered.length === 0 && (
            <div className="p-10 text-center">
              <div className="text-sm font-semibold">No emails match your filters</div>
              <div className="mt-1 text-sm text-slate-600">Try broadening your query or clearing filters.</div>
            </div>
          )}
        </div>
      </div>
      </div>
    </div>
  )
}
