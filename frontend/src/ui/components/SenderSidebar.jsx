import React from 'react'
import { Link } from 'react-router-dom'
import { listEmails, listSenders, listThreads } from '../../api/client'
import { Badge } from './Badge'
import { SenderCard } from './SenderCard'
import { SkeletonLine } from './Skeleton'

function formatDate(dateStr) {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleDateString()
  } catch {
    return String(dateStr)
  }
}

export function SenderSidebar({ onThreadSelect, selectedSenderId }) {
  const [senders, setSenders] = React.useState([])
  const [status, setStatus] = React.useState('idle') // idle | loading | success | error
  const [expandedId, setExpandedId] = React.useState(null)
  // Map of senderId → ThreadWithSenderResponse[]
  const [threadMap, setThreadMap] = React.useState({})
  const [loadingThreads, setLoadingThreads] = React.useState({})
  const [allExpanded, setAllExpanded] = React.useState(false)
  const [allStatus, setAllStatus] = React.useState('idle') // idle | loading | success | error
  const [allEmails, setAllEmails] = React.useState([])

  React.useEffect(() => {
    const controller = new AbortController()
    setStatus('loading')
    listSenders({ limit: 50, signal: controller.signal })
      .then((data) => {
        setSenders(Array.isArray(data) ? data : [])
        setStatus('success')
      })
      .catch((e) => {
        if (e?.name !== 'AbortError') setStatus('error')
      })
    return () => controller.abort()
  }, [])

  async function handleAllToggle() {
    onThreadSelect?.({ sender: null })
    setAllExpanded((v) => !v)
    if (allExpanded) return
    if (allStatus === 'success') return

    setAllStatus('loading')
    try {
      const res = await listEmails({ maxResults: 10 })
      const arr = Array.isArray(res) ? res : res?.emails || res?.items || []
      setAllEmails(Array.isArray(arr) ? arr : [])
      setAllStatus('success')
    } catch {
      setAllEmails([])
      setAllStatus('error')
    }
  }

  async function handleToggle(sender) {
    onThreadSelect?.({ sender })
    if (expandedId === sender.id) {
      setExpandedId(null)
      return
    }
    setExpandedId(sender.id)
    // Load threads lazily on first expand
    if (threadMap[sender.id] !== undefined) return
    setLoadingThreads((m) => ({ ...m, [sender.id]: true }))
    try {
      const threads = await listThreads({ senderId: sender.id, topK: 20 })
      setThreadMap((m) => ({
        ...m,
        [sender.id]: Array.isArray(threads) ? threads : [],
      }))
    } catch {
      setThreadMap((m) => ({ ...m, [sender.id]: [] }))
    } finally {
      setLoadingThreads((m) => ({ ...m, [sender.id]: false }))
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {/* "All" tile — always on top */}
      <div className={`overflow-hidden rounded-lg border shadow-sm transition-colors ${!selectedSenderId ? 'border-slate-300 bg-slate-100' : 'border-slate-200 bg-white'}`}>
        <button
          className="flex w-full items-center gap-3 p-3 text-left transition hover:bg-slate-50"
          onClick={handleAllToggle}
        >
          <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-semibold text-white select-none">
            All
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-slate-900">All</div>
            <div className="truncate text-xs text-slate-500">Last 10 emails</div>
          </div>
          <Badge tone="slate">10</Badge>
          <svg
            className={`h-4 w-4 flex-shrink-0 text-slate-400 transition-transform duration-150 ${allExpanded ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {allExpanded && (
          <div className="border-t border-slate-100">
            {allStatus === 'loading' ? (
              <div className="px-4 py-3">
                <SkeletonLine height="h-3" width="w-2/3" />
                <SkeletonLine height="h-3" width="w-1/2" className="mt-2" />
              </div>
            ) : allStatus === 'error' ? (
              <div className="px-4 py-3 text-xs text-rose-600">Failed to load last emails</div>
            ) : allEmails.length === 0 ? (
              <div className="px-4 py-3 text-xs text-slate-500">No emails found</div>
            ) : (
              allEmails.map((item) => {
                const email = item?.email || item?.mail || item || {}
                const id = email?.id
                const subject = email?.subject || '(No subject)'
                const date = email?.received_at || email?.date || ''
                const cls =
                  'flex w-full flex-col gap-0.5 border-b border-slate-50 px-4 py-2.5 text-left transition last:border-0 hover:bg-slate-50'
                return id ? (
                  <Link key={id} to={`/emails/${encodeURIComponent(id)}`} className={cls}>
                    <div className="truncate text-xs font-medium text-slate-800">{subject}</div>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      <span>{formatDate(date)}</span>
                    </div>
                  </Link>
                ) : (
                  <div key={subject + String(date)} className={cls}>
                    <div className="truncate text-xs font-medium text-slate-800">{subject}</div>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      <span>{formatDate(date)}</span>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        )}
      </div>

      {status === 'loading' &&
        Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <div className="flex items-center gap-3">
              <div className="h-9 w-9 flex-shrink-0 animate-pulse rounded-full bg-slate-200" />
              <div className="flex-1 space-y-1.5">
                <SkeletonLine height="h-3" width="w-2/3" />
                <SkeletonLine height="h-3" width="w-1/2" />
              </div>
            </div>
          </div>
        ))}

      {status === 'error' && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700">
          Failed to load senders
        </div>
      )}

      {status === 'success' && senders.length === 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-500">
          No senders yet. Refresh the inbox to load emails.
        </div>
      )}

      {senders.map((sender) => (
        <SenderCard
          key={sender.id}
          sender={sender}
          threads={
            loadingThreads[sender.id]
              ? []
              : (threadMap[sender.id] || [])
          }
          threadsLoaded={!!threadMap[sender.id]}
          isExpanded={expandedId === sender.id}
          isActive={selectedSenderId === sender.id}
          onToggle={() => handleToggle(sender)}
          onThreadClick={onThreadSelect}
        />
      ))}
    </div>
  )
}
