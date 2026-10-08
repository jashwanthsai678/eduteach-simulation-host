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
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
STORAGE_BUCKET = os.environ.get("SIMULATION_BUCKET", "simulations")
# Separate bucket from simulations -- different content type (PDF, not HTML),
# different lifecycle. Same Supabase project, must also be created+public.
PREP_SHEET_BUCKET = os.environ.get("PREP_SHEET_BUCKET", "prep-sheets")
# This service's own public URL, used to build the link handed back to the
# caller -- deliberately NOT Supabase's own storage URL, since that's the
# version with the broken content-type/CSP (see module docstring).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://eduteach-simulation-host.onrender.com").rstrip("/")

# Generous enough for a genuine interactive simulation (canvas/SVG + JS logic),
# small enough that one request can't meaningfully fill up storage.
_MAX_HTML_BYTES = 300_000

_DOCTYPE_RE = re.compile(r"<\s*html[\s>]", re.IGNORECASE)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "jinja"]),
)

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


class PrepImage(BaseModel):
    url: str
    caption: Optional[str] = None


class PrepWatchFor(BaseModel):
    text: str = Field(..., description="The likely misconception/mistake.")
    fix: str = Field(..., description="One-line way to catch or correct it.")


class PrepSection(BaseModel):
    title: str
    minutes: Optional[int] = None
    bullets: list[str] = Field(..., min_length=1)
    # At most one of these is expected to be set -- `image` for a single
    # illustrative image (e.g. Concept), `images` for several (e.g. Challenge).
    image: Optional[PrepImage] = None
    images: Optional[list[PrepImage]] = None
    watch: Optional[PrepWatchFor] = None


class PrepSheetRequest(BaseModel):
    """The 6-bucket lesson prep sheet. `refresher` and `real_life` are
    optional (omit if there's no previous lesson, or no real-life tie-in);
    `concept`, `challenge`, `level_set`, `explore` are always expected."""

    topic: str
    goal: Optional[str] = Field(None, description="One-line lesson objective.")
    floor: Optional[str] = Field(None, description="The weakest-child path/fallback.")
    refresher: Optional[PrepSection] = None
    concept: PrepSection
    real_life: Optional[PrepSection] = None
    challenge: PrepSection
    level_set: PrepSection
    explore: PrepSection


class CreatePrepSheetResponse(BaseModel):
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


def _render_prep_sheet_html(req: PrepSheetRequest) -> str:
    template = _jinja_env.get_template("prep_sheet.html.jinja")
    return template.render(
        topic=req.topic,
        goal=req.goal,
        floor=req.floor,
        refresher=req.refresher,
        concept=req.concept,
        real_life=req.real_life,
        challenge=req.challenge,
        level_set=req.level_set,
        explore=req.explore,
    )


def _html_to_pdf(html: str) -> bytes:
    # Fresh browser per request (not pooled) -- v1 keeps this simple; prep
    # sheets are low-volume (one per lesson, not one per chat message), so
    # launch/teardown cost isn't worth the complexity of a shared browser yet.
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 480, "height": 800})
            page.set_content(html, wait_until="networkidle")
            # The template's height depends on how much content is in each
            # section, so size the PDF page to the actual rendered content
            # instead of guessing a fixed page size.
            content_height = page.evaluate("document.body.scrollHeight")
            return page.pdf(width="480px", height=f"{content_height}px", print_background=True)
        finally:
            browser.close()


@app.post("/prep-sheets", response_model=CreatePrepSheetResponse)
def create_prep_sheet(req: PrepSheetRequest):
    html = _render_prep_sheet_html(req)

    try:
        pdf_bytes = _html_to_pdf(html)
    except Exception as exc:
        raise HTTPException(502, f"could not render prep sheet to PDF: {exc!r}")

    sheet_id = uuid.uuid4().hex
    storage_key = f"{sheet_id}.pdf"

    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/storage/v1/object/{PREP_SHEET_BUCKET}/{storage_key}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/pdf",
            },
            content=pdf_bytes,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach Supabase Storage: {exc!r}")
    if not resp.is_success:
        raise HTTPException(502, f"storage upload failed ({resp.status_code}): {resp.text}")

    return CreatePrepSheetResponse(id=sheet_id, url=f"{PUBLIC_BASE_URL}/p/{sheet_id}")


@app.get("/p/{sheet_id}")
def serve_prep_sheet(sheet_id: str):
    storage_key = f"{sheet_id}.pdf"
    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/storage/v1/object/public/{PREP_SHEET_BUCKET}/{storage_key}",
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach Supabase Storage: {exc!r}")
    # Same Supabase quirk as /s/{id}: a missing object comes back as HTTP 400
    # wrapping a NoSuchKey JSON body, not a plain 404.
    if resp.status_code == 404 or (resp.status_code == 400 and "NoSuchKey" in resp.text):
        raise HTTPException(404, "No prep sheet found at this link.")
    if not resp.is_success:
        raise HTTPException(502, f"storage fetch failed ({resp.status_code}): {resp.text}")
    return Response(content=resp.content, media_type="application/pdf")
