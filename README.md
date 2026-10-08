# EduTeach Simulation Host

Stores an AI-generated, self-contained interactive HTML/CSS/JS page (a
classroom simulation) and hands back a public URL. This service never
renders or executes the page itself -- it's a thin uploader. The simulation
runs entirely in whoever opens the link's own browser.

## How it fits in

```
Claude / ChatGPT (via eduteach-mcp's create_simulation tool)
        │  POST /simulations  {html: "<!doctype html>..."}
        ▼
eduteach-simulation-host (this repo)
        │  uploads the page to Supabase Storage
        ▼
Supabase Storage (public bucket)
        │  serves the raw HTML directly
        ▼
Teacher/student's own browser -- opens the link, the page runs there
```

Claude/ChatGPt and `eduteach-mcp` are only involved in *creating* the link.
Once it exists, viewing it is a plain, direct request from the viewer's
browser to Supabase Storage -- this service isn't even in that path.

## Endpoints

- `POST /simulations` -- body `{"html": "<!doctype html>...</html>"}` (a
  complete page, not a fragment). Returns `{"id": "...", "url": "..."}`.
  Rejects anything over 300KB or missing a real `<html>` tag.
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
  `SIMULATION_BUCKET` in Render's dashboard (never commit these).

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
