## Continued plan features (UI-side)

### Prioritization (sorting)

- Inbox now supports sorting:
    - By priority (high → medium → low → unknown), then by date
    - Newest first
- Implemented in `src/ui/pages/InboxPage.jsx`.

### Summary acceptance criteria helper (≤ 5 sentences)

- Email detail shows a warning badge if the summary is over 5 sentences and offers “Trim to 5”.
- Implemented in `src/ui/pages/EmailDetailPage.jsx`.

### Speech-to-text (Polish)

- Added STT language preference in Settings (`pl-PL` default, `en-US` optional).
- The Mic button now uses the selected language.
- Implemented in `src/ui/pages/SettingsPage.jsx`, `src/ui/pages/EmailDetailPage.jsx`, and defaulted in `src/ui/lib/storage.js`.

## Are _all_ features satisfied?

Some key items require backend support/data that isn’t currently available.

- Real RAG-based “Suggest response” + placeholders: no endpoint provided (UI scaffold exists only).
- Send email: no endpoint provided.
- History timeline with attachments: requires thread/message structure + attachments from backend.
- Sender priority from contacts DB: requires backend fields (contact status).
