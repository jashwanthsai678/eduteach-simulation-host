# EduTeach Simulation Host

Two related jobs, same hosting pattern:

1. **Simulations** -- stores an AI-generated, self-contained interactive
   HTML/CSS/JS page and hands back a public URL. This service never executes
   the page's logic -- that runs entirely in whoever opens the link's own
   browser -- but it does serve the page itself; see "Why this service
   exists" below for why that step can't just be Supabase's own storage URL.
2. **Prep sheets** -- takes structured lesson-prep content (topic, goal,
   and the 6 buckets: Refresher/Concept/Real Life/Challenge/Level Set/
   Explore), renders it into EduTeach's actual prep-sheet design (not
   whatever HTML the model guesses), converts that to a PDF, and hands back
   a public URL to the PDF.

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

```
Claude / ChatGPT (via eduteach-mcp's create_prep_sheet tool)
        │  POST /prep-sheets  {topic, goal, concept, challenge, ...}
        ▼
eduteach-simulation-host (this repo)
        │  fills app/templates/prep_sheet.html.jinja with the data
        │  (fixed, designed layout -- the model never writes HTML here)
        │  renders that HTML to PDF with headless Chromium (Playwright)
        │  uploads the PDF bytes to Supabase Storage
        ▼
Supabase Storage (separate public bucket: prep-sheets)
        │
GET /p/{id}  ◀── the link actually handed back to the caller
        ▼
Teacher opens the link, gets a real PDF (no CSP/content-type issue --
        that problem is specific to serving executable HTML, not PDFs)
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
- `POST /prep-sheets` -- body is the structured 6-bucket content (see
  `PrepSheetRequest` in `app/main.py` for the exact shape: `topic`, optional
  `goal`/`floor`, optional `refresher`/`real_life`, required `concept`/
  `challenge`/`level_set`/`explore`; each section is
  `{title, minutes?, bullets[], image?, images?, watch?}`). Returns
  `{"id": "...", "url": ".../p/{id}"}`. Rejects malformed shapes (e.g. a
  section with no bullets) with a normal 422.
- `GET /p/{id}` -- serves a previously created prep sheet as a real
  `application/pdf`. 404 if the id doesn't exist.
- `GET /health` -- plain health check.

## Prep sheets: why structured JSON in, PDF out (not HTML like simulations)

`create_simulation` intentionally lets the model write arbitrary HTML/CSS/JS,
because a simulation's whole point is open-ended interactivity -- there's no
one fixed design to target. A prep sheet is the opposite: EduTeach already
has one real, designed layout for it
(`app/templates/prep_sheet.html.jinja`, adapted from
`prep-sheet-template.html`). So the model's job is to produce the *content*
(topic, bullets, images, timings) as structured data, and this service's job
is to lay that out consistently every time -- the model never touches markup
here. The HTML is then rendered to PDF server-side with headless Chromium
(via Playwright), since the template uses flexbox layouts that a lighter
HTML-to-PDF library (e.g. WeasyPrint) doesn't reliably support.

The **Refresher** bucket (first section, only present if a previous lesson
was discussed earlier in the same chat) is populated by Claude/ChatGPT's own
conversation memory -- nothing in this service does that; it's just an
optional field the caller may or may not include.

## Setup

### 1. Create the Storage bucket (one-time, in Supabase's dashboard)

Uses the **same Supabase project** `eduteach-ingest-service` already writes
to -- no new Supabase project needed.

1. Supabase dashboard -> **Storage** -> **New bucket**.
2. Name: `simulations` (or set `SIMULATION_BUCKET` to whatever you pick).
3. **Public bucket: ON** -- this is required. Without it, uploaded pages
   won't be viewable by a plain browser link.
4. Repeat for a second bucket named `prep-sheets` (or set
   `PREP_SHEET_BUCKET` to whatever you pick) -- also **Public bucket: ON**.
   Prep sheets are PDFs, stored separately from simulation HTML.

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
python -m playwright install chromium   # downloads the browser binary prep sheets render with
uvicorn app.main:app --reload
```

### 4. Render deploy

**Prep sheets need this service deployed as a Docker environment, not
Render's native Python buildpack.** Playwright's Chromium needs several OS
packages (fonts, `libnss3`, `libatk`, etc.) that a native buildpack won't
install; the Dockerfile's `playwright install --with-deps chromium` step
handles that, but only runs if Render actually builds from the Dockerfile.

- In Render's dashboard, set this service's **Environment** to **Docker**
  (it will pick up the repo's `Dockerfile` automatically) -- if it's
  currently set to "Python 3" with the old `pip install` build command, that
  needs changing, since this is a deploy-config change, not just a code push.
- Environment variables: set `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` /
  `SIMULATION_BUCKET` / `PREP_SHEET_BUCKET` in Render's dashboard (never
  commit these). If this service's URL isn't
  `https://eduteach-simulation-host.onrender.com` (the default
  `PUBLIC_BASE_URL` assumes), also set `PUBLIC_BASE_URL` to whatever Render
  actually gives it -- otherwise the links handed back will point at the
  wrong host.
- Expect a noticeably heavier/slower build than before (Chromium + its
  system deps add real size) and a larger running memory footprint whenever
  a prep sheet is being rendered -- worth watching on a free-tier instance.

## Known limitations (deliberate, for v1)

- **Links don't auto-expire.** Nothing here deletes old simulations or prep
  sheets -- they persist in Storage until manually removed. A real
  expiry/cleanup job (e.g. a daily scheduled task deleting anything older
  than 30 days) is a reasonable follow-up, not built yet.
- **Prep sheets render with a fresh headless Chromium per request**, not a
  shared/pooled browser. Simple and correct, but slower than necessary under
  real concurrent load -- acceptable while usage is "a few per lesson," not
  "many per second."
- **No image-fetch validation on prep sheets.** `image.url`/`images[].url`
  are placed directly into the PDF's `<img src>` with no host allowlist (the
  MCP connector's `view_image` tool checks its own allowlist separately, but
  this service trusts whatever URL it's given) -- fine while callers are
  trusted (Claude/ChatGPT via the connector), not fine if this endpoint were
  ever exposed to arbitrary public input.
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
