/**
 * @file Card — white rounded-border container with optional CardHeader (title + description + actions)
 * and CardSection for dividing content areas within a card.
 */

import React from 'react'

export function Card({ children, className = '' }) {
    return (
        <div className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${className}`}>
            {children}
        </div>
    )
}

export function CardHeader({ title, description, actions }) {
    return (
        <div className="mb-3">
            {actions ? (
                <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                    <div>
                        <div className="text-base font-semibold">{title}</div>
                        {description && <div className="mt-1 text-sm text-slate-600">{description}</div>}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">{actions}</div>
                </div>
            ) : (
                <>
                    <div className="text-sm font-semibold">{title}</div>
                    {description && <div className="mt-1 text-sm text-slate-600">{description}</div>}
                </>
            )}
        </div>
    )
}

export function CardSection({ children, className = '' }) {
    return <div className={`mt-3 ${className}`}>{children}</div>
}