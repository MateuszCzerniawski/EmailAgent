import React from 'react'
import { createBrowserRouter } from 'react-router-dom'
import { RootLayout } from './ui/RootLayout'
import { InboxPage } from './ui/pages/InboxPage'
import { EmailDetailPage } from './ui/pages/EmailDetailPage'
import { SettingsPage } from './ui/pages/SettingsPage'
import { ComposePage } from './ui/pages/ComposePage'
import { ChatPage } from './ui/pages/ChatPage'

export const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [
      { path: '/', element: <InboxPage /> },
      { path: '/emails/:emailId', element: <EmailDetailPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '/write', element: <ComposePage /> },
      { path: '/chat', element: <ChatPage /> },
    ],
  },
])

