import React from 'react'

export const Input = React.forwardRef(function Input({ className = '', ...props }, ref) {
    return (
        <input
            ref={ref}
            className={`h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-slate-400 ${className}`}
            {...props}
        />
    )
})

export function Textarea({ className = '', rows = 4, style = {}, ...props }) {
    return (
        <textarea
            className={`w-full resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400 ${className}`}
            style={{ minHeight: rows * 35, ...style }}
            {...props}
        />
    )
}

export function Select({ children, className = '', ...props }) {
    return (
        <select
            className={`h-10 w-full rounded-md border border-slate-200 bg-white px-2 text-sm outline-none focus:border-slate-400 ${className}`}
            {...props}
        >
            {children}
        </select>
    )
}

export function Label({ children, className = '' }) {
    return <div className={`mb-1 text-xs font-medium text-slate-700 ${className}`}>{children}</div>
}

export function FormField({ label, children, className = '' }) {
    return (
        <label className={className}>
            {label && <Label>{label}</Label>}
            {children}
        </label>
    )
}
