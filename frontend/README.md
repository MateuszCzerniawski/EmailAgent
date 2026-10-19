# email Agent – Coordinator Inbox

Inteligentna skrzynka odbiorcza dla koordynatorów email, wykorzystująca AI do analizy e-maili i sugerowania odpowiedzi.

## Szybki start

```

# Instalacja zależnościnpm install# Uruchom dev server (http://localhost:5173)npm run dev# Uruchom CORS proxy (http://localhost:8001)npm run proxy
```

##  Wymagania

- **Node.js** ≥20.19.0 lub ≥22.12.0
- **npm** (lub yarn/pnpm)
- **Backend API** na `http://127.0.0.1:8000` (lub skonfiguruj `VITE_API_BASE_URL`)

## Technologie

- **React 19.2.4** – framework UI
- **Vite 8.0.1** – bundler
- **Tailwind CSS 4.2.2** – stylowanie
- **React Router 7.13.2** – routing
- **Web Speech API** – dyktowanie głosowe (pl-PL/en-US)

## Struktura projektu

```

src/
├── api/client.js          # HTTP client (fetch)
├── ui/
│   ├── components/        # Komponenty UI (Alert, Badge, Button, Card...)
│   ├── lib/               # Utilities (normalizacja, storage)
│   ├── pages/             # Strony (Inbox, EmailDetail, Settings)
│   └── RootLayout.jsx     # Layout + nawigacja
├── main.jsx               # Punkt wejścia
└── router.jsx             # Konfiguracja routingu
```

## Funkcje

### Inbox

- Lista e-maili z AI analizą (tagi, priorytet, podsumowanie)
- Filtry: tag, priorytet
- Sortowanie: priorytet/data

### Szczegóły e-maila

- Edycja metadanych AI (tags, priority, summary ≤5 zdań, actions)
- Podgląd ciała e-maila
- Historia wątku
- Sugerowanie odpowiedzi (UI ready, wymaga backendu)
- Dyktowanie głosowe (STT)

### Ustawienia

- Tryb modelu (Cloud/Local)
- Język STT (pl-PL/en-US)
- Test połączenia z backendem
- Zarządzanie lokalnym cache'm

## Konfiguracja

### Zmienne środowiskowe:

**Frontend (`.env`):**

```

VITE_API_BASE_URL=http://127.0.0.1:8001
```

**Proxy (`proxy-server.mjs`):**

```

BACKEND_ORIGIN=http://127.0.0.1:8000PROXY_PORT=8001CORS_ALLOW_ORIGIN=*
```

## Skrypty npm

```

npm run dev       # Vite dev servernpm run build     # Build produkcyjny (dist/)npm run preview   # Podgląd buildunpm run proxy     # Uruchom CORS proxy
```

## Ograniczenia (wymaga backendu)

- **RAG-based response generation** – brak endpointu
- **Wysyłanie e-maili** – brak `POST /send`
- **Zapisywanie edycji** – brak `PATCH /emails/:id`
- **Cache management** – brak `GET /cache/status`

## Bezpieczeństwo

**Nie używaj w produkcji bez:**

- Autentykacji (OAuth2/JWT)
- Szyfrowania localStorage (lub migracji do secure backend storage)
- Rate limiting na proxy

## Dokumentacja

Szczegółowa dokumentacja znajduje się w pliku `DOCUMENTATION.md` (w katalogu głównym).