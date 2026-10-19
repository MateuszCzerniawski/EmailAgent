import React from 'react'
import { generateSenderSummary, getSenderTags } from '../../api/client'
import { Badge } from './Badge'
import { Button } from './Button'
import { Card } from './Card'

function avatarBg(relationType) {
  if (relationType === 'vip') return 'bg-amber-500'
  if (relationType === 'internal') return 'bg-blue-600'
  if (relationType === 'client') return 'bg-violet-600'
  return 'bg-slate-500'
}

function relationTone(relationType) {
  if (relationType === 'vip') return 'amber'
  if (relationType === 'internal') return 'blue'
  if (relationType === 'client') return 'violet'
  return 'slate'
}

function initials(name, email) {
  return ((name || email || '?').trim().charAt(0)).toUpperCase()
}

const RefreshIcon = ({ spinning }) => spinning
  ? (
    <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  ) : (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
    </svg>
  )

export function SenderInfoPanel({ sender, threadCount, onSummaryUpdated }) {
  const [tags, setTags] = React.useState([])
  const [tagsStatus, setTagsStatus] = React.useState('idle')
  const [generating, setGenerating] = React.useState(false)

  React.useEffect(() => {
    if (!sender?.id) return
    const controller = new AbortController()
    setTagsStatus('loading')
    setTags([])
    getSenderTags(sender.id, { signal: controller.signal })
      .then((data) => {
        setTags(Array.isArray(data?.tags) ? data.tags : [])
        setTagsStatus('success')
      })
      .catch((e) => { if (e?.name !== 'AbortError') setTagsStatus('error') })
    return () => controller.abort()
  }, [sender?.id])

  async function handleGenerate() {
    if (!sender?.id || generating) return
    setGenerating(true)
    try {
      const result = await generateSenderSummary(sender.id)
      onSummaryUpdated?.(result.summary)
    } catch (err) {
      console.error('Sender summary generation failed:', err)
    } finally {
      setGenerating(false)
    }
  }

  if (!sender) return null

  const bg = avatarBg(sender.relation_type)
  const tone = relationTone(sender.relation_type)

  return (
    <Card>
      <div className="flex items-start gap-4">
        {/* Avatar */}
        <div className={`flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full ${bg} text-base font-semibold text-white select-none`}>
          {initials(sender.display_name, sender.email)}
        </div>

        <div className="flex-1 min-w-0">
          {/* Identity */}
          <div className="font-semibold text-slate-900 truncate">
            {sender.display_name || sender.email}
          </div>
          {sender.display_name && (
            <div className="text-xs text-slate-500 truncate">{sender.email}</div>
          )}

          {/* Badges */}
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge tone={tone}>{sender.relation_type || 'unknown'}</Badge>
            <Badge tone="blue">importance {sender.importance_score || 0}/5</Badge>
            <Badge tone="slate">{threadCount ?? 0} {threadCount === 1 ? 'thread' : 'threads'}</Badge>
            {tags.map((tag) => <Badge key={tag} tone="slate">{tag}</Badge>)}
            {tagsStatus === 'loading' && <span className="text-xs text-slate-400">loading tags…</span>}
            {tagsStatus === 'error' && <span className="text-xs text-rose-500">tags unavailable</span>}
          </div>

          {/* Summary */}
          <div className="mt-2">
            {sender.summary ? (
              <div className="flex items-start gap-2">
                <p className="flex-1 text-sm text-slate-700">{sender.summary}</p>
                <button
                  type="button"
                  className="flex-shrink-0 text-slate-400 hover:text-slate-700 transition p-0.5 rounded disabled:opacity-50"
                  title="Refresh sender summary"
                  disabled={generating}
                  onClick={handleGenerate}
                >
                  <RefreshIcon spinning={generating} />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-sm text-slate-400">(No summary available)</span>
                <Button variant="secondary" size="sm" disabled={generating} onClick={handleGenerate}>
                  {generating ? 'Generating…' : 'Generate'}
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}
