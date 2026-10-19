/**
 * @file Alert — inline feedback banner with four severity variants: info (default), warning, error, success.
 * Accepts an optional title, body children, and an actions slot for buttons.
 */

import React from 'react'

const variants = {
    info: 'border-slate-200 bg-slate-50 text-slate-900',
    warning: 'border-amber-200 bg-amber-50 text-amber-900',
    error: 'border-rose-200 bg-rose-50 text-rose-900',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900',
}

export function Alert({ variant = 'info', title, children, actions, className = '' }) {
    const variantClass = variants[variant] || variants.info

    return (
        <div className={`rounded-xl border p-4 text-sm ${variantClass} ${className}`}>
            {title && <div className="font-semibold">{title}</div>}
            {children && <div className="mt-1 opacity-90">{children}</div>}
            {actions && <div className="mt-3 flex gap-2">{actions}</div>}
        </div>
    )
}