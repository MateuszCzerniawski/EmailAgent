/**
 * @file Badge — small pill label used throughout the UI for tags, statuses, and scores.
 * Supported tones: slate (default), blue, green, amber, rose, violet.
 */

import React from 'react'

const stylesByTone = {
  slate: 'bg-slate-100 text-slate-800 ring-slate-200',
  blue: 'bg-blue-100 text-blue-800 ring-blue-200',
  green: 'bg-emerald-100 text-emerald-800 ring-emerald-200',
  amber: 'bg-amber-100 text-amber-900 ring-amber-200',
  rose: 'bg-rose-100 text-rose-900 ring-rose-200',
  violet: 'bg-violet-100 text-violet-900 ring-violet-200',
}

export function Badge({ children, tone = 'slate', className = '' }) {
  const cls = stylesByTone[tone] || stylesByTone.slate
  return (
    <span
      className={[
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        cls,
        className,
      ].join(' ')}
    >
      {children}
    </span>
  )
}

