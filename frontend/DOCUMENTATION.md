## Przegląd projektu

**email Agent** to aplikacja internetowa zbudowana w React 19 + Vite 8 + Tailwind CSS 4, która służy jako inteligentna skrzynka odbiorcza dla koordynatorów email. Aplikacja wykorzystuje AI do analizy e-maili, automatycznie dodając:

- **Tagi** (kategorie tematyczne)
- **Priorytet** (high/medium/low)
- **Podsumowania** (do 5 zdań)
- **Wyodrębnione akcje** (zadania do wykonania)

Użytkownik pełni rolę **moderatora** – przegląda, zatwierdza i edytuje sugestie AI przed wysłaniem odpowiedzi.

---

## Struktura katalogów

```

email_agent/
├── public/               # Zasoby statyczne (ikony, favicon)
├── src/
│   ├── api/
│   │   └── client.js     # Klient HTTP do backendu
│   ├── assets/           # Obrazy i grafiki
│   ├── ui/
│   │   ├── components/   # Komponenty UI (Alert, Badge, Button, Card, Form, InfoBox, Skeleton)
│   │   ├── lib/
│   │   │   ├── normalizeEmail.js  # Normalizacja danych e-mail z backendu
│   │   │   └── storage.js         # LocalStorage do nadpisań lokalnych
│   │   ├── pages/
│   │   │   ├── EmailDetailPage.jsx  # Szczegóły e-maila + edycja metadanych AI
│   │   │   ├── InboxPage.jsx        # Lista e-maili z filtrami i sortowaniem
│   │   │   └── SettingsPage.jsx     # Ustawienia (tryb modelu, STT, test połączenia)
│   │   └── RootLayout.jsx           # Layout główny (nagłówek + nawigacja)
│   ├── main.jsx          # Punkt wejścia React
│   ├── router.jsx        # Routing (react-router-dom)
│   └── styles.css        # Tailwind CSS
├── index.html
├── vite.config.js
├── package.json
├── proxy-server.mjs      # Proxy CORS do backendu (dev)
└── todo.md               # Plan funkcji i status implementacji
```

---

## Kluczowe funkcje

### 1. **Inbox (lista e-maili)**

- **Ścieżka:** `/`
- **Plik:** `src/ui/pages/InboxPage.jsx`
- **Funkcje:**
    - Wyświetla listę e-maili z tagami, priorytetem i podsumowaniem
    - Filtry: po tagu, priorytecie
    - Sortowanie: po priorytecie lub dacie (najnowsze)
    - Limit 2 e-maile (hard-coded, aby zminimalizować koszty tokenów w trybie testowym)
    - Odświeżanie listy przyciskiem "Refresh"

### 2. **Szczegóły e-maila**

- **Ścieżka:** `/emails/:emailId`
- **Plik:** `src/ui/pages/EmailDetailPage.jsx`
- **Funkcje:**
    - **Edycja metadanych AI:** tagi, priorytet, podsumowanie (z walidacją ≤5 zdań), akcje (z deadlinem)
    - **Podgląd ciała e-maila** (plaintext lub HTML)
    - **Historia wątku** (jeśli backend zwraca dane wątku)
    - **Sugerowanie odpowiedzi** (UI scaffold – brak endpointu backendu)
    - **Dyktowanie głosowe (STT)** dla draftu odpowiedzi (Web Speech API, język pl-PL/en-US)
    - **Przycisk "Reset edits"** – przywraca oryginalne dane AI
    - **Lokalne zapisywanie zmian** (localStorage, automatyczne po 350ms)

### 3. **Ustawienia**

- **Ścieżka:** `/settings`
- **Plik:** `src/ui/pages/SettingsPage.jsx`
- **Funkcje:**
    - **Tryb modelu:** Cloud (GPT-4o) / Local (Ollama) – tylko UI, wymaga backendu
    - **Język STT:** Polish (pl-PL) / English (en-US)
    - **Test połączenia z backendem:** `/emails?max_results=2`
    - **Zarządzanie cache'em:** UI ready (brak endpointu `/cache/status`)
    - **Czyszczenie lokalnych nadpisań** (localStorage)

---

## Komponenty UI

### **Alert** (`src/ui/components/Alert.jsx`)

- Warianty: `info`, `warning`, `error`, `success`
- Opcjonalny tytuł, tekst, akcje (przyciski)

### **Badge** (`src/ui/components/Badge.jsx`)

- Warianty kolorystyczne: `slate`, `blue`, `green`, `amber`, `rose`, `violet`
- Używane do: priorytetów, tagów, statusów

### **Button** (`src/ui/components/Button.jsx`)

- Warianty: `primary`, `secondary`, `ghost`, `danger`
- Rozmiary: `sm`, `md`

### **Card / CardHeader / CardSection** (`src/ui/components/Card.jsx`)

- Layout kontenerowy z ramką i cieniem
- `CardHeader` wspiera title + description + actions

### **Form (Input, Textarea, Select, Label, FormField)** (`src/ui/components/Form.jsx`)

- Komponenty formularzy z Tailwind styling

### **InfoBox** (`src/ui/components/InfoBox.jsx`)

- Szary box do wyświetlania info (np. ciało e-maila, notatki)

### **Skeleton** (`src/ui/components/Skeleton.jsx`)

- Animowane placeholder'y podczas ładowania

---

## API Client (`src/api/client.js`)

### `listEmails({ maxResults, query, signal })`

- **Endpoint:** `GET /emails?max_results=N&query=X`
- Zwraca listę e-maili z analizą AI

### `getEmail(emailId, { signal })`

- **Endpoint:** `GET /emails/:emailId`
- Zwraca szczegóły pojedynczego e-maila

### `testEmailsEndpoint({ signal })`

- **Endpoint:** `GET /emails?max_results=2`
- Test połączenia (używany w Settings)

**Uwaga:** Backend URL konfigurowany przez `VITE_API_BASE_URL` (domyślnie `http://127.0.0.1:8001`).

---

## Normalizacja danych (`src/ui/lib/normalizeEmail.js`)

### `normalizeEmailListItem(raw)`

Konwertuje dane z backendu na model UI:

```

{  id, subject, from, date, snippet,  analysis: { tags, priority, summary, actions, raw }}
```

### `normalizeEmailDetail(raw)`

Jak wyżej + dodaje pole `body` (plaintext/html).

### Mapowanie pól backendu:

- `category` → tag
- `sentiment` → tag
- `key_action_required: true` → priority: "high"
- `action_description` → actions[0].text

---

## LocalStorage (`src/ui/lib/storage.js`)

### **Nadpisania e-maili** (`email.overrides.v1`)

Struktura:

```

{  [emailId]: {    tags: string[],    priority: string,    summary: string,    actions: Array<{ text, due }>,    responseDraft: string,    _updatedAt: timestamp  }}
```

### **Ustawienia** (`email.settings.v1`)

```

{  modelMode: 'cloud' | 'local',  // default: 'cloud'  sttLang: 'pl-PL' | 'en-US'     // default: 'pl-PL'}
```

---

## Proxy CORS (`proxy-server.mjs`)

**Cel:** Obejście CORS w dev mode (frontend na `localhost:5173`, backend na `localhost:8000`).

**Uruchomienie:**

```

npm run proxy
```

**Działanie:**

- Nasłuchuje na `127.0.0.1:8001`
- Przekierowuje requesty do `BACKEND_ORIGIN` (default `http://127.0.0.1:8000`)
- **Ogranicza `max_results` do max 2** (zabezpieczenie przed kosztami tokenów)
- Dodaje nagłówki CORS: `Access-Control-Allow-Origin: *`

---

## Uruchomienie projektu

### **Wymagania:**

- Node.js ≥20.19.0 (lub ≥22.12.0)
- npm

### **Instalacja:**

```

npm install
```

### **Development:**

```

npm run dev         # Vite dev server (http://localhost:5173)npm run proxy       # CORS proxy (http://localhost:8001)
```

### **Build:**

```

npm run build       # Output: dist/npm run preview     # Podgląd produkcji
```

---

## Konfiguracja środowiskowa

### **Frontend (Vite):**

- `VITE_API_BASE_URL` – URL backendu (default: `http://127.0.0.1:8001`)

### **Proxy:**

- `BACKEND_ORIGIN` – backend URL (default: `http://127.0.0.1:8000`)
- `PROXY_PORT` – port proxy (default: `8001`)
- `CORS_ALLOW_ORIGIN` – CORS origin (default: `*`)

---

## Status implementacji (wg `todo.md`)

###  **Zaimplementowane:**

- Sortowanie Inbox (priorytet + data)
- Walidacja podsumowania (≤5 zdań) + przycisk "Trim to 5"
- STT (Speech-to-Text) w języku polskim/angielskim
- Lokalne edycje metadanych (tags, priority, summary, actions)
- Historia wątku (jeśli backend dostarcza dane)
- Test połączenia z backendem

### **Wymaga backendu:**

- **RAG-based "Suggest response"** – brak endpointu (tylko UI scaffold)
- **Wysyłanie e-maili** – brak endpointu
- **Priorytet nadawcy z bazy kontaktów** – backend nie zwraca pola
- **Cache management** – UI ready, brak endpointu `/cache/status`
- **Zapisywanie edycji na serwerze** – brak `PATCH /emails/:id`

---

## Zgodność z przeglądarkami

- **React 19.2.4** – wymaga nowoczesnych przeglądarek (Chrome 90+, Firefox 88+, Safari 14+)
- **Web Speech API (STT)** – Chrome/Edge (pełne wsparcie), Firefox/Safari (częściowe/brak)
- **Tailwind CSS 4** – nie wymaga runtime, działa wszędzie

---

## Bezpieczeństwo i prywatność

### **LocalStorage:**

- Wszystkie edycje i drafty zapisywane lokalnie (brak automatycznego syncu z serwerem)
- Dane przechowywane w przeglądarce – **nie szyfrowane** (ryzyko XSS)

### **CORS Proxy:**

- **Dev-only** – nie używać w produkcji (brak autentykacji/rate limiting)

### **API Client:**

- Brak autentykacji (nie zaimplementowano tokenów/cookies)
- Wymaga dodania warstwy auth przed wdrożeniem produkcyjnym

---

## Roadmap

1. **Backend endpoints:**

    - `PATCH /emails/:id` – zapisywanie edycji metadanych
    - `POST /emails/:id/send` – wysyłanie odpowiedzi
    - `GET /cache/status` – status cache'a SQLite
    - `POST /suggest-response` – RAG-based generowanie odpowiedzi