# EduTeach Simulation Host

Stores an AI-generated, self-contained interactive HTML/CSS/JS page (a
classroom simulation) and hands back a public URL. This service never
executes the page's logic -- that runs entirely in whoever opens the link's
own browser -- but it does serve the page itself; see "Why this service
exists" below for why that step can't just be Supabase's own storage URL.

## How it fits in

```
Claude / ChatGPT (via eduteach-mcp's create_simulation tool)
        │  POST /simulations  {html: "<!doctype html>..."}
        ▼
eduteach-simulation-host (this repo)
        │  uploads the raw bytes to Supabase Storage
        ▼
Supabase Storage (public bucket) -- durable storage only, never
        │  linked to directly (see below)
        │
GET /s/{id}  ◀── the link actually handed back to the caller
        │  fetches the bytes from Storage server-side,
        │  re-serves them with the correct content-type/CSP
        ▼
Teacher/student's own browser -- opens the link, the page runs there
```

## Why this service exists (not just "upload to Storage and link directly")

Confirmed on real data: Supabase Storage serves an uploaded `.html` object
back as `Content-Type: text/plain` with a locked-down
`Content-Security-Policy: default-src 'none'; sandbox` header -- a standard
anti-abuse measure most cloud storage providers apply to uploaded HTML, to
stop their storage from being usable as a general web-hosting/script
platform. Even fixing the content-type, that CSP blocks every script from
running, which would make an "interactive" simulation inert. So Storage is
used purely as durable bytes-storage; `GET /s/{id}` is what the viewer's
browser actually talks to, re-serving those bytes with the correct
content-type and no restrictive CSP.

## Endpoints

- `POST /simulations` -- body `{"html": "<!doctype html>...</html>"}` (a
  complete page, not a fragment). Returns `{"id": "...", "url": ".../s/{id}"}`
  -- a link to THIS service, not to Supabase directly. Rejects anything over
  300KB or missing a real `<html>` tag.
- `GET /s/{id}` -- serves a previously created simulation as a real
  `text/html` page. 404 if the id doesn't exist.
- `GET /health` -- plain health check.

## Setup

### 1. Create the Storage bucket (one-time, in Supabase's dashboard)

Uses the **same Supabase project** `eduteach-ingest-service` already writes
to -- no new Supabase project needed.

1. Supabase dashboard -> **Storage** -> **New bucket**.
2. Name: `simulations` (or set `SIMULATION_BUCKET` to whatever you pick).
3. **Public bucket: ON** -- this is required. Without it, uploaded pages
   won't be viewable by a plain browser link.

### 2. Environment variables

Copy `.env.example` to `.env` and fill in the **same** `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` values already used by `eduteach-ingest-service`
(check that repo's own Render environment settings, or wherever you have
them saved -- they're not repeated here since they're secrets).

### 3. Local run

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 4. Render deploy

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment: set `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` /
  `SIMULATION_BUCKET` in Render's dashboard (never commit these). If this
  service's URL isn't `https://eduteach-simulation-host.onrender.com` (the
  default `PUBLIC_BASE_URL` assumes), also set `PUBLIC_BASE_URL` to whatever
  Render actually gives it -- otherwise the links handed back will point at
  the wrong host.

## Known limitations (deliberate, for v1)

- **Links don't auto-expire.** Nothing here deletes old simulations --
  they persist in Storage until manually removed. A real expiry/cleanup
  job (e.g. a daily scheduled task deleting anything older than 30 days)
  is a reasonable follow-up, not built yet.
- **No content moderation.** Anything passed in gets uploaded and served
  publicly as-is, same open/unauthenticated posture as the rest of
  EduTeach's APIs today. The size cap and `<html>`-tag check are basic
  sanity checks, not a security boundary.
- **Runs in the viewer's browser with no sandboxing beyond the browser's
  own.** Since the HTML/JS only ever executes client-side (never on this
  service or on EduTeach's servers), there's no server-side code-execution
  risk -- but a broken or adversarial simulation could still do anything a
  normal web page can do in the viewer's browser (make network requests,
  etc.). Acceptable for an internal educational tool; would need real CSP/
  sandboxing hardening before wider public exposure.
