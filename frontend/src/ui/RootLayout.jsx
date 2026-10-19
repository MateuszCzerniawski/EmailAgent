import React from 'react'
import { NavLink, Outlet } from 'react-router-dom'

export function RootLayout() {
  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-10 border-b border-slate-200/70 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-slate-900 text-white">
              <span className="text-sm font-semibold">DK</span>
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold">Coordinator Inbox</div>
              <div className="text-xs text-slate-500">Context-first moderation</div>
            </div>
          </div>
          <nav className="flex items-center gap-1">
            <NavLink
              to="/"
              className={({ isActive }) =>
                [
                  'rounded-md px-3 py-2 text-sm font-medium',
                  isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100',
                ].join(' ')
              }
            >
              Inbox
            </NavLink>
            <NavLink
              to="/chat"
              className={({ isActive }) =>
                [
                  'rounded-md px-3 py-2 text-sm font-medium',
                  isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100',
                ].join(' ')
              }
            >
              Chat
            </NavLink>
            <NavLink
              to="/write"
              className={({ isActive }) =>
                [
                  'rounded-md px-3 py-2 text-sm font-medium',
                  isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100',
                ].join(' ')
              }
            >
              Write
            </NavLink>
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                [
                  'rounded-md px-3 py-2 text-sm font-medium',
                  isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100',
                ].join(' ')
              }
            >
              Settings
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}

