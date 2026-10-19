/**
 * @file ChatPage — conversational AI interface over the email + task knowledge base.
 *
 * Sends user messages to POST /chat, which performs parallel RAG retrieval (email chunks +
 * task chunks) and returns a reply, source citations, and optional clarifying questions.
 *
 * Features:
 *   - Streaming-style UX: messages appended to state as they arrive.
 *   - Citation cards: each source is rendered with subject, sender, date, score badge, and
 *     an "Open" link. Task sources show an amber "Task" badge and deadline if present.
 *   - Clarifying questions: when the model cannot answer, it returns up to 5 question chips.
 *     Clicking a chip pre-fills the input and focuses it so the user can send or edit.
 *   - Abort: the in-flight request can be cancelled with the Stop button (AbortController).
 *   - New session: clears all messages and resets state.
 *   - Disclaimer: "Chat bot can make mistakes — verify important information."
 */

import React from 'react'
import { Button } from '../components/Button'
import { Card, CardHeader, CardSection } from '../components/Card'
import { Badge } from '../components/Badge'
import { Alert } from '../components/Alert'
import { Input } from '../components/Form'
import { SkeletonLine } from '../components/Skeleton'
import { sendChatMessage } from '../../api/client'

export function ChatPage() {
    const [messages, setMessages] = React.useState([])
    const [input, setInput] = React.useState('')
    const [loading, setLoading] = React.useState(false)
    const [error, setError] = React.useState('')
    const bottomRef = React.useRef(null)
    const abortRef = React.useRef(null)
    const inputRef = React.useRef(null)

    React.useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, loading])

    function newSession() {
        abortRef.current?.abort()
        setMessages([])
        setInput('')
        setError('')
        setLoading(false)
    }

    function handleQuestionClick(question) {
        setInput(question)
        inputRef.current?.focus()
    }

    async function handleSend(e) {
        e.preventDefault()
        const text = input.trim()
        if (!text || loading) return

        const userMsg = { role: 'user', content: text }
        const prevMessages = [...messages]
        setMessages(prev => [...prev, userMsg])
        setInput('')
        setError('')
        setLoading(true)

        abortRef.current = new AbortController()
        try {
            const historyForApi = prevMessages.map(m => ({ role: m.role, content: m.content }))
            const res = await sendChatMessage(
                { message: text, history: historyForApi },
                { signal: abortRef.current.signal }
            )
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: res.reply,
                citations: res.citations || [],
                clarifyingQuestions: res.clarifying_questions || [],
            }])
        } catch (err) {
            if (err?.name === 'AbortError') return
            setError(String(err?.message || err))
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="flex flex-col gap-4">
            <Card>
                <CardHeader
                    title="Chat"
                    description="Ask questions about your emails. Answers are grounded in your inbox."
                    actions={
                        <Button variant="secondary" size="sm" onClick={newSession}>
                            New session
                        </Button>
                    }
                />
            </Card>

            <Card>
                <div className="flex flex-col gap-3 min-h-96 max-h-[600px] overflow-y-auto px-1 py-2">
                    <div className="text-xs text-slate-400 text-center pt-1">Chat bot can make mistakes — verify important information.</div>
                    {messages.length === 0 && !loading && (
                        <div className="flex items-center justify-center py-16 text-sm text-slate-400">
                            Ask anything about your inbox.
                        </div>
                    )}
                    {messages.map((msg, idx) => (
                        <MessageBubble key={idx} msg={msg} onQuestionClick={handleQuestionClick} />
                    ))}
                    {loading && (
                        <div className="flex flex-col gap-2 max-w-2xl">
                            <SkeletonLine height="h-4" width="w-3/4" />
                            <SkeletonLine height="h-4" width="w-1/2" />
                        </div>
                    )}
                    {error && <Alert variant="error">{error}</Alert>}
                    <div ref={bottomRef} />
                </div>

                <CardSection>
                    <form onSubmit={handleSend} className="flex items-center gap-2">
                        <div className="flex-1">
                            <Input
                                ref={inputRef}
                                value={input}
                                onChange={e => setInput(e.target.value)}
                                placeholder="Ask about your emails…"
                                disabled={loading}
                            />
                        </div>
                        <Button variant="primary" type="submit" disabled={!input.trim() || loading}>
                            {loading ? 'Thinking…' : 'Send'}
                        </Button>
                    </form>
                </CardSection>
            </Card>
        </div>
    )
}

function MessageBubble({ msg, onQuestionClick }) {
    const [showCitations, setShowCitations] = React.useState(false)
    const isUser = msg.role === 'user'

    return (
        <div className={`flex flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}>
            <div className={[
                'max-w-2xl rounded-xl px-4 py-2 text-sm whitespace-pre-wrap',
                isUser
                    ? 'bg-slate-900 text-white rounded-br-sm'
                    : 'bg-slate-100 text-slate-900 rounded-bl-sm',
            ].join(' ')}>
                {msg.content}
            </div>

            {!isUser && msg.clarifyingQuestions?.length > 0 && (
                <div className="max-w-2xl mt-1">
                    <p className="text-xs text-slate-500 mb-1.5">To help me answer better:</p>
                    <div className="flex flex-wrap gap-1.5">
                        {msg.clarifyingQuestions.map((q, i) => (
                            <button
                                key={i}
                                type="button"
                                onClick={() => onQuestionClick(q)}
                                className="text-xs rounded-full border border-slate-300 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50 hover:border-slate-400 transition-colors text-left"
                            >
                                {q}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {msg.citations && msg.citations.length > 0 && (
                <div className="max-w-2xl w-full">
                    <button
                        type="button"
                        className="mt-1 flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700"
                        onClick={() => setShowCitations(v => !v)}
                    >
                        <Badge tone="slate">
                            {msg.citations.length} source{msg.citations.length > 1 ? 's' : ''}
                        </Badge>
                        <span>{showCitations ? '▲ hide' : '▼ show'}</span>
                    </button>
                    {showCitations && (
                        <div className="mt-2 space-y-2">
                            {msg.citations.map((c, i) => (
                                <CitationCard key={i} citation={c} />
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

function CitationCard({ citation }) {
    const isTask = citation.source_type === 'task'
    return (
        <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
                {isTask && <Badge tone="amber">Task</Badge>}
                <span className="font-medium text-slate-800">{citation.subject}</span>
                <Badge tone="blue">{citation.sender}</Badge>
                <Badge tone="violet">score {citation.score}</Badge>
                {citation.received_at && (
                    <span className="text-slate-400">{citation.received_at}</span>
                )}
                {citation.email_id ? (
                    <Button
                        as="a"
                        variant="secondary"
                        size="sm"
                        href={`/emails/${encodeURIComponent(citation.email_id)}`}
                        target="_blank"
                        rel="noreferrer"
                    >
                        Open
                    </Button>
                ) : null}
            </div>
            {isTask && citation.task_deadline && (
                <div className="text-amber-700 font-medium">
                    Deadline: {citation.task_deadline}
                </div>
            )}
            <p className="text-slate-600 leading-relaxed">{citation.snippet}</p>
        </div>
    )
}
