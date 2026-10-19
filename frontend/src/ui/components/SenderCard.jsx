import React from 'react'
import { Link } from 'react-router-dom'
import { Badge } from './Badge'

function initials(name, email) {
  const src = (name || email || '?').trim()
  return src.charAt(0).toUpperCase()
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleDateString()
  } catch {
    return dateStr
  }
}

function avatarBg(relationType) {
  if (relationType === 'vip') return 'bg-amber-500'
  if (relationType === 'internal') return 'bg-blue-600'
  if (relationType === 'client') return 'bg-violet-600'
  return 'bg-slate-500'
}

function activeStyle(relationType) {
  if (relationType === 'vip') return 'border-amber-300 bg-amber-50'
  if (relationType === 'internal') return 'border-blue-300 bg-blue-50'
  if (relationType === 'client') return 'border-violet-300 bg-violet-50'
  return 'border-slate-300 bg-slate-50'
}

export function SenderCard({ sender, threads, threadsLoaded, isExpanded, isActive, onToggle, onThreadClick }) {
  const relationTone =
    sender.relation_type === 'vip' ? 'amber' :
    sender.relation_type === 'internal' ? 'blue' :
    sender.relation_type === 'client' ? 'violet' :
    'slate'

  return (
    <div className={`overflow-hidden rounded-lg border shadow-sm transition-colors ${isActive ? activeStyle(sender.relation_type) : 'border-slate-200 bg-white'}`}>
      <button
        className="flex w-full items-center gap-3 p-3 text-left transition hover:bg-slate-50"
        onClick={onToggle}
      >
        {/* Avatar */}
        <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full ${avatarBg(sender.relation_type)} text-sm font-semibold text-white select-none`}>
          {initials(sender.display_name, sender.email)}
        </div>

        {/* Sender info */}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-900">
            {sender.display_name || sender.email}
          </div>
          {sender.display_name && (
            <div className="truncate text-xs text-slate-500">{sender.email}</div>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <Badge tone={relationTone}>{sender.relation_type || 'unknown'}</Badge>
            <Badge tone="blue">importance {sender.importance_score || 0}/5</Badge>
          </div>
        </div>

        {/* Thread/mail count */}
        <Badge tone="slate" title={threadsLoaded ? `${threads.length} threads` : `${sender.mail_count ?? 0} emails`}>
          {threadsLoaded ? threads.length : (sender.mail_count ?? 0)}
        </Badge>

        {/* Chevron */}
        <svg
          className={`h-4 w-4 flex-shrink-0 text-slate-400 transition-transform duration-150 ${isExpanded ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isExpanded && (
        <div className="border-t border-slate-100">
          {threads.length === 0 ? (
            <div className="px-4 py-3 text-xs text-slate-500">No threads found</div>
          ) : (
            threads.map((item) => {
              const inner = (
                <>
                  <div className="truncate text-xs font-medium text-slate-800">
                    {item.thread.subject || '(No subject)'}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <span>{formatDate(item.thread.last_mail_at)}</span>
                    {item.thread.mail_count > 1 && (
                      <span>{item.thread.mail_count} emails</span>
                    )}
                  </div>
                </>
              )
              const cls = 'flex w-full flex-col gap-0.5 border-b border-slate-50 px-4 py-2.5 text-left transition last:border-0 hover:bg-slate-50'
              return item.latest_email_id ? (
                <Link
                  key={item.thread.id}
                  to={`/emails/${encodeURIComponent(item.latest_email_id)}`}
                  className={cls}
                  onClick={() => onThreadClick?.(item)}
                >
                  {inner}
                </Link>
              ) : (
                <div key={item.thread.id} className={cls}>
                  {inner}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
