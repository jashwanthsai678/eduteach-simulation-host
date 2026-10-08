"""Stores AI-generated interactive HTML/CSS/JS pages (classroom simulations)
and hands back a public URL. Does NOT render or execute anything itself --
the page runs entirely in whoever opens the link's own browser.

Content lands in Supabase Storage (same account the rest of EduTeach
already uses for textbook images), but this service -- not Supabase's own
storage URL -- is what the browser actually requests the page from. That's
deliberate: confirmed on real data that Supabase serves stored .html objects
back as `Content-Type: text/plain` with a locked-down
`Content-Security-Policy: default-src 'none'; sandbox` header -- a standard
anti-abuse measure most cloud storage providers apply to uploaded HTML,
which would block ALL scripts from running even if the content-type were
right. That defeats the entire point of an *interactive* simulation, so
GET /s/{id} below fetches the raw bytes from Storage server-side and
re-serves them itself with the correct content-type and no restrictive CSP.
"""

import os
import re
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
STORAGE_BUCKET = os.environ.get("SIMULATION_BUCKET", "simulations")
# This service's own public URL, used to build the link handed back to the
# caller -- deliberately NOT Supabase's own storage URL, since that's the
# version with the broken content-type/CSP (see module docstring).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://eduteach-simulation-host.onrender.com").rstrip("/")

# Generous enough for a genuine interactive simulation (canvas/SVG + JS logic),
# small enough that one request can't meaningfully fill up storage.
_MAX_HTML_BYTES = 300_000

_DOCTYPE_RE = re.compile(r"<\s*html[\s>]", re.IGNORECASE)

app = FastAPI(title="EduTeach Simulation Host")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateSimulationRequest(BaseModel):
    html: str = Field(..., description="A complete, self-contained HTML page.")


class CreateSimulationResponse(BaseModel):
    id: str
    url: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/simulations", response_model=CreateSimulationResponse)
def create_simulation(req: CreateSimulationRequest):
    html_bytes = req.html.encode("utf-8")
    if not html_bytes.strip():
        raise HTTPException(400, "html must not be empty")
    if len(html_bytes) > _MAX_HTML_BYTES:
        raise HTTPException(
            400,
            f"html is {len(html_bytes)} bytes, over the {_MAX_HTML_BYTES}-byte limit -- "
            "simplify the page (e.g. avoid embedding large base64 assets).",
        )
    if not _DOCTYPE_RE.search(req.html):
        raise HTTPException(400, "html must contain a real <html> tag -- pass a complete page, not a fragment")

    sim_id = uuid.uuid4().hex
    storage_key = f"{sim_id}.html"

    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{storage_key}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "text/html; charset=utf-8",
            },
            content=html_bytes,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        # A connection-level failure (bad SUPABASE_URL, DNS, timeout) raises here
        # rather than returning a response -- surface the real cause instead of
        # letting FastAPI's default 500 ("Internal Server Error") mask it.
        raise HTTPException(502, f"could not reach Supabase Storage: {exc!r}")
    if not resp.is_success:
        raise HTTPException(502, f"storage upload failed ({resp.status_code}): {resp.text}")

    return CreateSimulationResponse(id=sim_id, url=f"{PUBLIC_BASE_URL}/s/{sim_id}")


@app.get("/s/{sim_id}")
def serve_simulation(sim_id: str):
    storage_key = f"{sim_id}.html"
    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{storage_key}",
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach Supabase Storage: {exc!r}")
    # Confirmed on real data: Supabase Storage returns HTTP 400 (not a plain
    # 404) wrapping a JSON body like {"statusCode":"404",...,"code":"NoSuchKey"}
    # for a missing object -- check the actual signal, not just the outer status.
    if resp.status_code == 404 or (resp.status_code == 400 and "NoSuchKey" in resp.text):
        raise HTTPException(404, "No simulation found at this link.")
    if not resp.is_success:
        raise HTTPException(502, f"storage fetch failed ({resp.status_code}): {resp.text}")
    return Response(content=resp.content, media_type="text/html")
