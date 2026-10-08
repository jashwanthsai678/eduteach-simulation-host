"""Stores AI-generated interactive HTML/CSS/JS pages (classroom simulations)
and hands back a public URL. Does NOT render or execute anything itself --
the page runs entirely in whoever opens the link's own browser. This service
is just a thin uploader to Supabase Storage, same storage account the rest
of EduTeach already uses for textbook images.
"""

import os
import re
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
STORAGE_BUCKET = os.environ.get("SIMULATION_BUCKET", "simulations")

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

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{storage_key}"
    return CreateSimulationResponse(id=sim_id, url=public_url)
