/**
 * @file Skeleton — animated loading placeholder.
 * SkeletonLine renders a single pulsing bar; width and height accept any Tailwind sizing class.
 */

import React from 'react'

export function SkeletonLine({ width = 'w-full', height = 'h-4', className = '' }) {
  return <div className={`animate-pulse rounded bg-slate-100 ${height} ${width} ${className}`} />
}
