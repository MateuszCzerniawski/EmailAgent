import React from 'react'

export function InfoBox({ children, className = '' }) {
    return (
        <div className={`rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 ${className}`}>
            {children}
        </div>
    )
}