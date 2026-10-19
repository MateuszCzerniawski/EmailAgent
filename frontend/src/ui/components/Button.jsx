/**
 * @file Button — polymorphic button primitive with four variants (primary, secondary, ghost, danger)
 * and two sizes (sm, md). Renders as a `<button>` by default; pass `as={Link}` to render as a link.
 */

import React from 'react'

const variants = {
  primary: 'bg-slate-900 text-white hover:bg-slate-800 focus-visible:outline-slate-900',
  secondary:
    'bg-white text-slate-900 ring-1 ring-inset ring-slate-200 hover:bg-slate-50 focus-visible:outline-slate-400',
  ghost: 'bg-transparent text-slate-900 hover:bg-slate-100 focus-visible:outline-slate-400',
  danger: 'bg-rose-600 text-white hover:bg-rose-500 focus-visible:outline-rose-600',
}

const sizes = {
  sm: 'h-8 px-3 text-sm',
  md: 'h-9 px-3.5 text-sm',
}

export function Button({
  as: As = 'button',
  variant = 'secondary',
  size = 'md',
  className = '',
  ...props
}) {
  return (
    <As
      className={[
        'inline-flex items-center justify-center gap-2 rounded-md font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50 disabled:pointer-events-none',
        variants[variant] || variants.secondary,
        sizes[size] || sizes.md,
        className,
      ].join(' ')}
      {...props}
    />
  )
}

