/**
 * @file ComposePage — simple email compose form.
 *
 * Validates recipient address format (EMAIL_RE) and requires a non-empty subject and body
 * before enabling Send. Calls sendEmail() from the API client; shows success/error feedback inline.
 */

import React from 'react'
import { Card, CardHeader } from '../components/Card'
import { Button } from '../components/Button'
import { Alert } from '../components/Alert'
import { FormField, Input, Textarea } from '../components/Form'
import { sendEmail } from '../../api/client'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function ComposePage() {
  const [to, setTo] = React.useState('')
  const [subject, setSubject] = React.useState('')
  const [body, setBody] = React.useState('')
  const [sendStatus, setSendStatus] = React.useState('idle') // idle | sending | success | error
  const [sendError, setSendError] = React.useState(null)

  const toValid = EMAIL_RE.test(to.trim())
  const canSend = toValid && subject.trim().length > 0 && body.trim().length > 0

  async function handleSend() {
    if (!canSend || sendStatus === 'sending') return
    const confirmed = window.confirm(`Send email to ${to.trim()}?\n\nSubject: ${subject.trim()}`)
    if (!confirmed) return

    setSendStatus('sending')
    setSendError(null)
    try {
      await sendEmail({ to: to.trim(), subject: subject.trim(), body: body.trim() })
      setSendStatus('success')
      setTo('')
      setSubject('')
      setBody('')
    } catch (err) {
      setSendError(err?.message || 'Failed to send')
      setSendStatus('error')
    } finally {
      if (sendStatus !== 'success') setSendStatus('idle')
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader title="New email" description="Compose and send a new message." />
        <div className="space-y-4">
          <FormField label="To">
            <Input
              type="email"
              placeholder="recipient@example.com"
              value={to}
              onChange={(e) => { setTo(e.target.value); setSendStatus('idle') }}
            />
            {to.length > 0 && !toValid && (
              <p className="mt-1 text-xs text-rose-500">Enter a valid email address.</p>
            )}
          </FormField>
          <FormField label="Subject">
            <Input
              placeholder="Subject"
              value={subject}
              onChange={(e) => { setSubject(e.target.value); setSendStatus('idle') }}
            />
          </FormField>
          <FormField label="Message">
            <Textarea
              rows={10}
              placeholder="Write your message here…"
              value={body}
              onChange={(e) => { setBody(e.target.value); setSendStatus('idle') }}
            />
          </FormField>
          {sendStatus === 'success' && (
            <Alert variant="success">Email sent successfully.</Alert>
          )}
          {sendError && (
            <Alert variant="error">{sendError}</Alert>
          )}
          <div className="flex justify-end">
            <Button
              variant="primary"
              disabled={!canSend || sendStatus === 'sending'}
              onClick={handleSend}
            >
              {sendStatus === 'sending' ? 'Sending…' : 'Send'}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}
