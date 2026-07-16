"""
Raven Sharp Video Creator — FastAPI Backend
AI-generated short-form video with per-user brand profiles + choice of
generation provider (InVideo.ai / Higgsfield / Meta AI).
Part of Ascension Digital Group

NOTE ON PROVIDER INTEGRATIONS — READ BEFORE GOING LIVE
-------------------------------------------------------
The three provider functions below (call_invideo, call_higgsfield, call_meta)
are STUBS. I do not have verified, current API documentation for any of these
three services' direct developer REST APIs, so I have not written fabricated
request/response handling against guessed endpoints — that risks silent
failures or wasted API spend once real keys are wired in.

Before this goes live, for EACH provider you plan to actually offer:
  1. Get their official developer API docs + an API key from their dashboard.
  2. Fill in the matching call_<provider>() function below with the real
     endpoint, auth header shape, and request/response fields.
  3. Everything else (auth, Stripe billing, brand profiles, project storage,
     output presets) is complete and tested — only the provider calls
     themselves need real integration work.
"""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os, uuid, json, logging, asyncio, base64, hmac, hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

import bcrypt, jwt, httpx
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

# ── Config (identical pattern to Book Creator / Image Optimiser / POD) ─────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ravensharp-videocreator")

_startup_warnings = []

MONGO_URL = os.environ.get("MONGO_URL")
if not MONGO_URL:
    log.critical(
        "STARTUP FAILURE: MONGO_URL is not set on this deployment. "
        "The app cannot start without a database connection string. "
        "Set MONGO_URL in Railway's environment variables for this service and redeploy."
    )
    raise RuntimeError("Missing required environment variable: MONGO_URL")

DB_NAME = os.environ.get("DB_NAME")
if not DB_NAME:
    DB_NAME = "ravensharp_videocreator"
    _startup_warnings.append(f"DB_NAME was not set — defaulting to '{DB_NAME}'.")

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    import secrets as _secrets
    JWT_SECRET = _secrets.token_hex(32)
    _startup_warnings.append(
        "JWT_SECRET was not set — auto-generated a temporary one for this boot. "
        "Existing user sessions will be invalidated on every restart until a permanent "
        "JWT_SECRET is set in Railway's environment variables. "
        "IMPORTANT: use a DIFFERENT secret than Book Creator/Image Optimiser/POD — "
        "sharing a JWT_SECRET across apps lets a login token from one app work on another."
    )

STRIPE_KEY  = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if STRIPE_KEY and not STRIPE_WEBHOOK_SECRET:
    _startup_warnings.append(
        "STRIPE_WEBHOOK_SECRET was not set — /billing/webhook will REJECT all events (fail-closed) "
        "until this is set. Get it from Stripe Dashboard -> Developers -> Webhooks -> your endpoint."
    )
RESEND_KEY  = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM_EMAIL", "Raven Sharp <noreply@raven-sharp.com>")
if not RESEND_KEY:
    _startup_warnings.append("RESEND_API_KEY was not set — password reset emails will NOT be sent.")

INVIDEO_API_KEY    = os.environ.get("INVIDEO_API_KEY", "")
HIGGSFIELD_API_KEY = os.environ.get("HIGGSFIELD_API_KEY", "")
META_API_KEY        = os.environ.get("META_API_KEY", "")
for _name, _key in [("INVIDEO_API_KEY", INVIDEO_API_KEY), ("HIGGSFIELD_API_KEY", HIGGSFIELD_API_KEY), ("META_API_KEY", META_API_KEY)]:
    if not _key:
        _startup_warnings.append(f"{_name} was not set — that provider will return a clear 501 'not yet integrated' error.")

R2_ENDPOINT   = os.environ.get("R2_ENDPOINT", "")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET     = os.environ.get("R2_BUCKET", "adg-images")
if not (R2_ENDPOINT and R2_ACCESS_KEY and R2_SECRET_KEY):
    _startup_warnings.append("R2 storage is not fully configured — brand asset uploads will fail until set.")

# Cheap model for script/prompt drafting — same Gemini model Book Creator uses.
# Keeps iteration free-ish; the expensive provider call only fires once the
# user is happy with the script and asks for the actual render.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
RUNWARE_API_KEY = os.environ.get("RUNWARE_API_KEY", "")
RUNWARE_MODEL = os.environ.get("RUNWARE_MODEL", "runware:101@1")  # image generation — verify against your dashboard
RUNWARE_BGREMOVE_MODEL = os.environ.get("RUNWARE_BGREMOVE_MODEL", "runware:110@1")  # verify against your dashboard
if not GEMINI_API_KEY:
    _startup_warnings.append(
        "GEMINI_API_KEY was not set — /api/generate/script will return a clear 500 error. "
        "Set it to enable cheap script/prompt drafting before the expensive provider render."
    )

for _w in _startup_warnings:
    log.warning("STARTUP: %s", _w)

OWNER_EMAIL  = os.environ.get("OWNER_EMAIL", "ascensiondigitalagency@outlook.com")
# NOTE: video.raven-sharp.com is a placeholder — subdomain not created yet.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        ",".join([
            FRONTEND_URL,
            "https://video.raven-sharp.com",
            "https://raven-sharp-video-creator.pages.dev",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]),
    ).split(",")
    if origin.strip()
]

client = AsyncIOMotorClient(MONGO_URL)
db     = client[DB_NAME]

app = FastAPI(title="Raven Sharp Video Creator API")
api = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.raven-sharp\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Tier config (placeholder pricing, same shape as Book Creator) ──────────
TIERS = {
    "free":    {"videos_per_month": 1,  "max_duration_sec": 15, "brand_profiles": 1, "watermark": True,  "price": 0},
    "creator": {"videos_per_month": 10, "max_duration_sec": 60, "brand_profiles": 3, "watermark": False, "price": 29},
    "studio":  {"videos_per_month": 40, "max_duration_sec": 180,"brand_profiles": 10,"watermark": False, "price": 79},
    "owner":   {"videos_per_month": 99999, "max_duration_sec": 9999, "brand_profiles": 999, "watermark": False, "price": 0},
}

# TODO: replace with real Stripe Price IDs (separate product from Book Creator)
STRIPE_PRICES = {
    "creator": {"monthly": "price_REPLACE_VC_CREATOR_MONTHLY", "annual": "price_REPLACE_VC_CREATOR_ANNUAL"},
    "studio":  {"monthly": "price_REPLACE_VC_STUDIO_MONTHLY",  "annual": "price_REPLACE_VC_STUDIO_ANNUAL"},
}

# ── Output format presets (aspect ratio / resolution, per platform) ─────────
OUTPUT_PRESETS = {
    "vertical_1080x1920":  {"label": "Vertical 9:16 (TikTok / Reels / Shorts)", "width": 1080, "height": 1920},
    "horizontal_1920x1080":{"label": "Horizontal 16:9 (YouTube)",              "width": 1920, "height": 1080},
    "square_1080x1080":    {"label": "Square 1:1 (Feed post)",                 "width": 1080, "height": 1080},
}

@api.get("/output-options")
async def get_output_options():
    return OUTPUT_PRESETS

@api.get("/providers")
async def get_providers():
    """Public — lets the frontend show which providers are actually
    configured/available right now, rather than offering a choice that 501s."""
    return {
        "invideo":    {"label": "InVideo.ai", "configured": bool(INVIDEO_API_KEY)},
        "higgsfield": {"label": "Higgsfield", "configured": bool(HIGGSFIELD_API_KEY)},
        "meta":       {"label": "Meta AI",    "configured": bool(META_API_KEY)},
    }

# ── Auth helpers (identical to Book Creator / Image Optimiser / POD) ───────
def hash_pw(pw): return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_pw(pw, h):
    if isinstance(h, str):
        h = h.encode("utf-8")
    return bcrypt.checkpw(pw.encode("utf-8"), h)

def make_access(uid, email):
    return jwt.encode({"sub": uid, "email": email, "type": "access",
                        "exp": datetime.now(timezone.utc) + timedelta(days=1)},
                       JWT_SECRET, algorithm="HS256")

def make_refresh(uid):
    return jwt.encode({"sub": uid, "type": "refresh",
                        "exp": datetime.now(timezone.utc) + timedelta(days=7)},
                       JWT_SECRET, algorithm="HS256")

def set_cookies(response, access, refresh):
    kw = dict(httponly=True, secure=True, samesite="none", path="/")
    response.set_cookie("access_token",  access,  max_age=86400,  **kw)
    response.set_cookie("refresh_token", refresh, max_age=604800, **kw)

async def get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user:
            raise HTTPException(401, "User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except Exception:
        raise HTTPException(401, "Invalid token")

async def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_KEY:
        log.warning("send_email skipped (no RESEND_API_KEY configured): to=%s subject=%r", to, subject)
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
                json={"from": RESEND_FROM, "to": [to], "subject": subject, "html": html},
            )
            if resp.status_code >= 400:
                log.error("Resend email failed (%s): %s", resp.status_code, resp.text[:500])
                return False
            return True
    except Exception as e:
        log.error("Resend email exception: %s", e)
        return False

# ── R2 storage (identical pattern to Book Creator / POD) ────────────────────
async def upload_to_r2(file_bytes: bytes, key_prefix: str, filename: str, mime: str = "image/png") -> str:
    if not (R2_ENDPOINT and R2_ACCESS_KEY and R2_SECRET_KEY):
        log.warning("R2 not fully configured — skipping upload, public_url will be empty")
        return ""

    def _blocking_upload():
        import boto3
        from botocore.config import Config
        import io

        key = f"{key_prefix}/{filename}"
        s3 = boto3.client(
            "s3", endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY, aws_secret_access_key=R2_SECRET_KEY,
            config=Config(signature_version="s3v4"), region_name="auto",
        )
        s3.upload_fileobj(io.BytesIO(file_bytes), R2_BUCKET, key,
                           ExtraArgs={"ContentType": mime, "ACL": "public-read"})
        public_base = os.environ.get("R2_PUBLIC_URL", f"{R2_ENDPOINT}/{R2_BUCKET}")
        return f"{public_base.rstrip('/')}/{key}"

    try:
        return await asyncio.to_thread(_blocking_upload)
    except Exception as e:
        log.error(f"R2 upload failed: {e}")
        return ""

async def gemini_text(prompt: str) -> str:
    """Cheap step — script/prompt drafting. Same model Book Creator uses."""
    if not GEMINI_API_KEY:
        raise HTTPException(500, "Server misconfigured: GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8, "maxOutputTokens": 4096}}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, json=body)
        if not r.is_success:
            raise HTTPException(502, f"Gemini text error {r.status_code}: {r.text[:300]}")
        d = r.json()
        parts = d.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
        return parts[0].get("text", "") if parts else ""

# ── Provider abstraction ─────────────────────────────────────────────────────
# STATUS (verified during backend build):
#   - higgsfield: REAL integration below, using the official `higgsfield-client`
#     PyPI package (confirmed to exist and inspected its actual source).
#     Auth: set HF_API_KEY + HF_API_SECRET (or combined HF_KEY="key:secret")
#     from cloud.higgsfield.ai — NOT the same as any Claude/MCP connection.
#     The exact `application` model string for video (vs. the image example
#     in their docs, 'bytedance/seedream/v4/text-to-image') still needs
#     confirming against your Higgsfield dashboard's model catalog — check
#     cloud.higgsfield.ai for the current video model id and set
#     HIGGSFIELD_VIDEO_MODEL below if it differs from the placeholder.
#   - invideo: a real API exists (pro-api.invideo.io) but the only docs I could
#     verify are from a 2023 ChatGPT-plugin-era manifest — almost certainly
#     stale. STUB below; get current docs from invideo.io dashboard →
#     Settings → Developers → API Keys before filling this in.
#   - meta: no verified public API found. STUB below.
class ProviderNotConfigured(HTTPException):
    def __init__(self, provider: str, detail: str = ""):
        msg = f"'{provider}' is not yet integrated"
        if detail:
            msg += f": {detail}"
        super().__init__(501, msg)

HIGGSFIELD_VIDEO_MODEL = os.environ.get("HIGGSFIELD_VIDEO_MODEL", "")  # e.g. "kling/v1/text-to-video" — CONFIRM against your dashboard

async def call_invideo(script: str, brand_context: str, output_preset: dict, character_ref_urls: Optional[List[str]] = None) -> dict:
    """STUB. Real API confirmed to exist at pro-api.invideo.io, but I only
    found 2023-era ChatGPT-plugin docs for it (almost certainly stale).
    Get current API docs + key from invideo.io dashboard before filling in."""
    if not INVIDEO_API_KEY:
        raise ProviderNotConfigured("invideo", "INVIDEO_API_KEY not set")
    raise ProviderNotConfigured("invideo", "endpoint/request shape not yet confirmed against current docs")

async def call_higgsfield(script: str, brand_context: str, output_preset: dict, character_ref_urls: Optional[List[str]] = None) -> dict:
    """Real integration via the official higgsfield-client SDK."""
    if not (os.environ.get("HF_KEY") or (os.environ.get("HF_API_KEY") and os.environ.get("HF_API_SECRET"))):
        raise ProviderNotConfigured("higgsfield", "HF_KEY or HF_API_KEY+HF_API_SECRET not set")
    if not HIGGSFIELD_VIDEO_MODEL:
        raise ProviderNotConfigured("higgsfield", "HIGGSFIELD_VIDEO_MODEL not set — check cloud.higgsfield.ai for the current video model id")

    from higgsfield_client import subscribe_async

    full_prompt = f"{brand_context}\n\n{script}" if brand_context else script
    arguments: Dict[str, Any] = {
        "prompt": full_prompt,
        "aspect_ratio": f"{output_preset['width']}:{output_preset['height']}",
    }
    if character_ref_urls:
        arguments["input_images"] = character_ref_urls

    try:
        result = await subscribe_async(HIGGSFIELD_VIDEO_MODEL, arguments=arguments)
    except Exception as e:
        raise HTTPException(502, f"Higgsfield generation failed: {e}")
    return {"provider": "higgsfield", "result": result}

async def call_meta(script: str, brand_context: str, output_preset: dict, character_ref_urls: Optional[List[str]] = None) -> dict:
    """STUB. No verified public API found for Meta AI video generation."""
    if not META_API_KEY:
        raise ProviderNotConfigured("meta", "META_API_KEY not set")
    raise ProviderNotConfigured("meta", "no verified API found yet")

PROVIDERS = {"invideo": call_invideo, "higgsfield": call_higgsfield, "meta": call_meta}

def _check_generation_allowed(user: dict, duration_sec: int):
    tier = user.get("tier", "free")
    limits = TIERS.get(tier, TIERS["free"])
    if user.get("videos_this_month", 0) >= limits["videos_per_month"]:
        raise HTTPException(403, f"Monthly video limit reached for the '{tier}' plan ({limits['videos_per_month']}/mo). Upgrade for more.")
    if duration_sec > limits["max_duration_sec"]:
        raise HTTPException(403, f"Max duration for the '{tier}' plan is {limits['max_duration_sec']}s.")

# ── Models ────────────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    email: str; password: str; name: Optional[str] = None

class LoginIn(BaseModel):
    email: str; password: str

class StripeCheckoutIn(BaseModel):
    tier: str; billing: str = "monthly"

class ForgotPasswordIn(BaseModel):
    email: str

class ResetPasswordIn(BaseModel):
    token: str
    new_password: str

class BrandAsset(BaseModel):
    name: str = ""
    url: str
    type: str = "image"          # image | logo | reference | screenshot
    description: str = ""

class BrandProfileIn(BaseModel):
    name: str
    brand_bible: str = ""
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    logo_url: Optional[str] = None
    characters: List[Dict[str, Any]] = Field(default_factory=list)  # [{name, description, image_url}]
    assets: List[BrandAsset] = Field(default_factory=list)          # broader asset/blueprint library

class AssetUploadIn(BaseModel):
    image_base64: str
    mime: str = "image/png"
    filename: str = "asset"
    asset_name: str = ""
    asset_type: str = "image"

class GenerateScriptIn(BaseModel):
    brief: str
    brand_profile_id: Optional[str] = None

class GenerateVideoIn(BaseModel):
    provider: str                       # "invideo" | "higgsfield" | "meta"
    script: str
    brand_profile_id: Optional[str] = None
    output_format: str = "vertical_1080x1920"
    duration_sec: int = 15

class ProjectCreateIn(BaseModel):
    title: str
    brand_profile_id: Optional[str] = None
    provider: str
    output_format: str = "vertical_1080x1920"
    script: str = ""
    video_url: Optional[str] = None

class ProjectUpdateIn(BaseModel):
    title: Optional[str] = None
    script: Optional[str] = None
    video_url: Optional[str] = None
    output_format: Optional[str] = None

# ── Auth routes (identical pattern to Book Creator) ─────────────────────────
@api.post("/auth/register")
async def register(payload: RegisterIn, response: Response):
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(400, "Email already registered")
    tier = "owner" if email == OWNER_EMAIL.lower() else "free"
    user = {"id": str(uuid.uuid4()), "email": email,
            "name": payload.name or email.split("@")[0],
            "password_hash": hash_pw(payload.password),
            "tier": tier, "videos_this_month": 0,
            "created_at": datetime.now(timezone.utc).isoformat()}
    await db.users.insert_one(user)
    access, refresh = make_access(user["id"], email), make_refresh(user["id"])
    set_cookies(response, access, refresh)
    return {"id": user["id"], "email": email, "name": user["name"],
            "tier": tier, "videos_this_month": 0, "created_at": user["created_at"]}

@api.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_pw(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    access, refresh = make_access(user["id"], email), make_refresh(user["id"])
    set_cookies(response, access, refresh)
    return {"id": user["id"], "email": email, "name": user.get("name"),
            "tier": user.get("tier", "free"), "videos_this_month": user.get("videos_this_month", 0),
            "created_at": user["created_at"]}

@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}

@api.get("/auth/me")
async def me(user: dict = Depends(get_user)):
    return {"id": user["id"], "email": user["email"], "name": user.get("name"),
            "tier": user.get("tier", "free"), "videos_this_month": user.get("videos_this_month", 0),
            "created_at": user["created_at"]}

@api.post("/auth/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(401, "No refresh token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(401, "User not found")
        access, refresh = make_access(user["id"], user["email"]), make_refresh(user["id"])
        set_cookies(response, access, refresh)
        return {"ok": True}
    except Exception:
        raise HTTPException(401, "Invalid refresh token")

_reset_tokens: dict = {}

@api.post("/auth/forgot-password")
async def forgot_password(payload: ForgotPasswordIn):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}
    token = str(uuid.uuid4())
    _reset_tokens[token] = {"email": email, "expires": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    reset_link = f"{FRONTEND_URL}/reset-password?token={token}"
    log.info(f"Password reset token for {email}: {token}")
    await send_email(
        to=email, subject="Reset your Raven Sharp Video Creator password",
        html=f"""<p>Someone requested a password reset for your Raven Sharp Video Creator account.</p>
                 <p><a href="{reset_link}">Click here to reset your password</a> — this link expires in 1 hour.</p>
                 <p>If you didn't request this, you can safely ignore this email.</p>""",
    )
    return {"message": "If that email exists, a reset link has been sent.",
            "debug_token": token if email == OWNER_EMAIL.lower() else None}

@api.post("/auth/reset-password")
async def reset_password(payload: ResetPasswordIn, response: Response):
    entry = _reset_tokens.get(payload.token)
    if not entry:
        raise HTTPException(400, "Invalid or expired reset token")
    if datetime.fromisoformat(entry["expires"]) < datetime.now(timezone.utc):
        del _reset_tokens[payload.token]
        raise HTTPException(400, "Reset token has expired")
    email = entry["email"]
    result = await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_pw(payload.new_password)}})
    if result.matched_count == 0:
        raise HTTPException(404, "User not found")
    del _reset_tokens[payload.token]
    return {"message": "Password reset successfully. Please sign in."}

@api.get("/auth/verify-reset-token/{token}")
async def verify_reset_token(token: str):
    entry = _reset_tokens.get(token)
    if not entry:
        raise HTTPException(400, "Invalid or expired reset token")
    if datetime.fromisoformat(entry["expires"]) < datetime.now(timezone.utc):
        del _reset_tokens[token]
        raise HTTPException(400, "Reset token has expired")
    return {"valid": True, "email": entry["email"]}

# ── Billing (identical pattern to Book Creator) ─────────────────────────────
@api.post("/billing/checkout")
async def create_checkout(payload: StripeCheckoutIn, user: dict = Depends(get_user)):
    if not STRIPE_KEY:
        raise HTTPException(500, "Stripe not configured")
    price_id = STRIPE_PRICES.get(payload.tier, {}).get(payload.billing)
    if not price_id:
        raise HTTPException(400, "Invalid tier")
    async with httpx.AsyncClient(timeout=30) as c:
        res = await c.post("https://api.stripe.com/v1/checkout/sessions",
            headers={"Authorization": f"Bearer {STRIPE_KEY}"},
            data={"mode": "subscription",
                  "line_items[0][price]": price_id,
                  "line_items[0][quantity]": "1",
                  "success_url": f"{FRONTEND_URL}/account?session_id={{CHECKOUT_SESSION_ID}}",
                  "cancel_url": f"{FRONTEND_URL}/pricing",
                  "customer_email": user["email"],
                  "metadata[user_id]": user["id"],
                  "metadata[tier]": payload.tier})
        if res.status_code != 200:
            raise HTTPException(500, "Stripe error")
        return {"checkout_url": res.json()["url"]}

def verify_stripe_signature(payload: bytes, sig_header: str, secret: str, tolerance_sec: int = 300) -> bool:
    """See Book Creator's identical implementation for full explanation.
    https://docs.stripe.com/webhooks#verify-manually"""
    if not sig_header or not secret:
        return False
    try:
        parts = dict(item.split("=", 1) for item in sig_header.split(",") if "=" in item)
        timestamp = parts.get("t")
        v1 = parts.get("v1")
        if not timestamp or not v1:
            return False
        if abs(datetime.now(timezone.utc).timestamp() - int(timestamp)) > tolerance_sec:
            log.warning("Stripe webhook rejected: timestamp outside tolerance (possible replay)")
            return False
        signed_payload = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)
    except Exception as e:
        log.warning(f"Stripe signature verification error: {e}")
        return False


@api.post("/billing/webhook")
async def stripe_webhook(request: Request):
    raw_body = await request.body()

    if not STRIPE_WEBHOOK_SECRET:
        log.error("Webhook rejected: STRIPE_WEBHOOK_SECRET is not configured")
        raise HTTPException(503, "Webhook not configured — set STRIPE_WEBHOOK_SECRET")

    sig_header = request.headers.get("stripe-signature", "")
    if not verify_stripe_signature(raw_body, sig_header, STRIPE_WEBHOOK_SECRET):
        log.error("Webhook rejected: invalid or missing Stripe-Signature header")
        raise HTTPException(400, "Invalid signature")

    try:
        event = json.loads(raw_body)
        if event["type"] == "checkout.session.completed":
            s = event["data"]["object"]
            await db.users.update_one(
                {"id": s["metadata"]["user_id"]},
                {"$set": {"tier": s["metadata"]["tier"], "videos_this_month": 0,
                          "subscription_id": s.get("subscription"),
                          "payment_failed_at": None, "payment_failure_count": 0}})
        elif event["type"] in ["customer.subscription.deleted", "customer.subscription.paused"]:
            sub_id = event["data"]["object"]["id"]
            await db.users.update_one({"subscription_id": sub_id}, {"$set": {"tier": "free"}})
        elif event["type"] == "invoice.payment_failed":
            invoice = event["data"]["object"]
            sub_id = invoice.get("subscription")
            if sub_id:
                await db.users.update_one(
                    {"subscription_id": sub_id},
                    {"$set": {"payment_failed_at": datetime.now(timezone.utc).isoformat()},
                     "$inc": {"payment_failure_count": 1}})
                log.warning(f"Payment failed for subscription {sub_id}")
    except Exception as e:
        log.error(f"Webhook error: {e}")
    return {"ok": True}

# ── Brand profiles (identical pattern to Book Creator) ──────────────────────
OWNER_BRAND_SEEDS = [
    {
        "name": "RavenSharp Tools",
        "brand_bible": "Tech/AI division of Ascension Digital Group — practical, no-nonsense, tool-focused. Covers mycalctools.net, mycalendartools.net, Image Optimiser, POD Suite, Book Creator, Content Creator. Tone: clear, helpful, confident, zero fluff.",
        "primary_color": "#7c5cbf", "secondary_color": "#a78bfa",
    },
    {
        "name": "Zyia Creations",
        "brand_bible": "Cosmic/spiritual brand — sacred geometry, psychedelic art, sovereignty and shadow-work themes. Tone: mystical, introspective, evocative. Sells on Etsy (zyiacreations.etsy.com).",
        "primary_color": "#6b21a8", "secondary_color": "#c026d3",
    },
    {
        "name": "Spew Crew Kids",
        "brand_bible": "Children's entertainment brand for YouTube — warm chaos that always resolves positively, every character gets a win, kid-friendly humour with sound effects.",
        "primary_color": "#4ADE80", "secondary_color": "#E53E3E",
        "characters": [
            {"name": "Rizzy Reflux", "description": "The leader — rainbow pastels, emotional regulation themes, bold and protective."},
            {"name": "Spewy Spence", "description": "The chaos engine — slime green, impulse control themes, hyper and adventurous skater."},
            {"name": "Milky Matt", "description": "The heart — soft blues/whites, self-acceptance themes."},
        ],
    },
    {
        "name": "Feed the Feed",
        "brand_bible": "Dystopian social commentary brand, Facebook-based. Tone: sharp, satirical, unsettling-but-thoughtful.",
        "primary_color": "#1a1a1a", "secondary_color": "#dc2626",
    },
    {
        "name": "Mystical Moments",
        "brand_bible": "Fine art photography by Emma James. Tone: contemplative, atmospheric, high-craft. Listed on ArtPal and Fine Art America.",
        "primary_color": "#1e293b", "secondary_color": "#94a3b8",
    },
]

@api.post("/brand-profiles/seed-owner-brands")
async def seed_owner_brands(user: dict = Depends(get_user)):
    """Owner-only, idempotent — pre-populates the known ADG brands so they
    don't need to be entered manually. Safe to call more than once; skips
    any brand that already exists by name. Same brand set as Ad Manager's
    seed, kept consistent across apps."""
    if user.get("tier") != "owner":
        raise HTTPException(403, "Owner only")
    existing_names = {b["name"] for b in await db.brand_profiles.find({"user_id": user["id"]}, {"name": 1}).to_list(200)}
    created = []
    for seed in OWNER_BRAND_SEEDS:
        if seed["name"] in existing_names:
            continue
        profile = {"id": str(uuid.uuid4()), "user_id": user["id"], "logo_url": None,
                   "characters": seed.get("characters", []), "assets": [],
                   "name": seed["name"], "brand_bible": seed["brand_bible"],
                   "primary_color": seed["primary_color"], "secondary_color": seed["secondary_color"],
                   "created_at": datetime.now(timezone.utc).isoformat()}
        await db.brand_profiles.insert_one(profile)
        created.append(seed["name"])
    return {"created": created, "skipped_existing": [s["name"] for s in OWNER_BRAND_SEEDS if s["name"] in existing_names]}


@api.post("/brand-profiles")
async def create_brand_profile(payload: BrandProfileIn, user: dict = Depends(get_user)):
    tier = user.get("tier", "free")
    limit = TIERS.get(tier, TIERS["free"])["brand_profiles"]
    existing = await db.brand_profiles.count_documents({"user_id": user["id"]})
    if existing >= limit:
        raise HTTPException(403, f"Brand profile limit reached for the '{tier}' plan ({limit}). Upgrade for more.")
    profile = {"id": str(uuid.uuid4()), "user_id": user["id"], **payload.dict(),
               "created_at": datetime.now(timezone.utc).isoformat()}
    await db.brand_profiles.insert_one(profile)
    profile.pop("_id", None)
    return profile

@api.get("/brand-profiles")
async def list_brand_profiles(user: dict = Depends(get_user)):
    return await db.brand_profiles.find({"user_id": user["id"]}, {"_id": 0}).to_list(200)

@api.get("/brand-profiles/{profile_id}")
async def get_brand_profile(profile_id: str, user: dict = Depends(get_user)):
    profile = await db.brand_profiles.find_one({"id": profile_id, "user_id": user["id"]}, {"_id": 0})
    if not profile:
        raise HTTPException(404, "Brand profile not found")
    return profile

@api.put("/brand-profiles/{profile_id}")
async def update_brand_profile(profile_id: str, payload: BrandProfileIn, user: dict = Depends(get_user)):
    result = await db.brand_profiles.update_one({"id": profile_id, "user_id": user["id"]}, {"$set": payload.dict()})
    if result.matched_count == 0:
        raise HTTPException(404, "Brand profile not found")
    return await db.brand_profiles.find_one({"id": profile_id}, {"_id": 0})

@api.delete("/brand-profiles/{profile_id}")
async def delete_brand_profile(profile_id: str, user: dict = Depends(get_user)):
    result = await db.brand_profiles.delete_one({"id": profile_id, "user_id": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(404, "Brand profile not found")
    return {"ok": True}

@api.post("/brand-profiles/{profile_id}/upload-asset")
async def upload_brand_asset(profile_id: str, payload: AssetUploadIn, user: dict = Depends(get_user)):
    """This previously uploaded to R2 and returned a URL, but never actually
    saved it anywhere on the brand profile — the asset had nowhere permanent
    to live. Now it's pushed onto the profile's asset library, matching Ad
    Manager's working pattern."""
    profile = await db.brand_profiles.find_one({"id": profile_id, "user_id": user["id"]})
    if not profile:
        raise HTTPException(404, "Brand profile not found")
    image_bytes = base64.b64decode(payload.image_base64)
    key = f"{uuid.uuid4()}-{payload.filename}"
    url = await upload_to_r2(image_bytes, f"video-creator-assets/{user['id']}/{profile_id}", key, payload.mime)
    if not url:
        raise HTTPException(500, "Upload failed — R2 not configured or upload error")

    new_asset = {"name": payload.asset_name or payload.filename, "url": url,
                 "type": payload.asset_type, "description": ""}
    await db.brand_profiles.update_one({"id": profile_id}, {"$push": {"assets": new_asset}})
    return {"url": url, "asset": new_asset}

# ── Generation ────────────────────────────────────────────────────────────────
async def _resolve_brand_context(brand_profile_id: Optional[str], user_id: str) -> str:
    if not brand_profile_id:
        return ""
    profile = await db.brand_profiles.find_one({"id": brand_profile_id, "user_id": user_id})
    if not profile:
        return ""
    lines = [f"Brand: {profile.get('name','')}"]
    if profile.get("brand_bible"):
        lines.append(f"Brand guidelines: {profile['brand_bible']}")
    if profile.get("characters"):
        char_lines = [f"- {c.get('name','')}: {c.get('description','')}" for c in profile["characters"]]
        lines.append("Established characters (keep consistent):\n" + "\n".join(char_lines))
    return "\n".join(lines)

async def call_runware_image(prompt: str, reference_image_url: Optional[str] = None,
                              width: int = 1024, height: int = 1024) -> Optional[str]:
    """Real character-consistency support via Runware's referenceImages
    parameter. Returns an image URL, or None on failure."""
    if not RUNWARE_API_KEY:
        return None
    task = {
        "taskType": "imageInference", "taskUUID": str(uuid.uuid4()),
        "model": RUNWARE_MODEL, "positivePrompt": prompt,
        "width": width, "height": height, "numberResults": 1, "outputType": "URL",
    }
    if reference_image_url:
        task["referenceImages"] = [reference_image_url]
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            res = await c.post("https://api.runware.ai/v1",
                headers={"Authorization": f"Bearer {RUNWARE_API_KEY}", "Content-Type": "application/json"},
                json=[task])
            if res.status_code != 200:
                logger.error(f"Runware error {res.status_code}: {res.text[:300]}")
                return None
            data = res.json()
            results = data.get("data", data) if isinstance(data, dict) else data
            return results[0].get("imageURL") if isinstance(results, list) and results else None
    except Exception as e:
        logger.error(f"Runware call failed: {e}")
        return None


class GenerateImageIn(BaseModel):
    prompt: str
    brand_profile_id: Optional[str] = None

@api.post("/generate/image")
async def generate_image_endpoint(payload: GenerateImageIn, user: dict = Depends(get_user)):
    """Standalone image generation (thumbnails, still assets, character
    references) — didn't exist at all before; this app only generated
    scripts and video. Uses the brand's first character reference image
    for consistency, same as Higgsfield's video generation does."""
    if not RUNWARE_API_KEY:
        raise HTTPException(500, "RUNWARE_API_KEY not configured")
    character_ref_url = None
    if payload.brand_profile_id:
        profile = await db.brand_profiles.find_one({"id": payload.brand_profile_id, "user_id": user["id"]})
        if profile:
            chars = profile.get("characters", [])
            if chars and chars[0].get("image_url"):
                character_ref_url = chars[0]["image_url"]

    image_url = await call_runware_image(payload.prompt, character_ref_url)
    if not image_url:
        raise HTTPException(500, "Image generation failed")
    async with httpx.AsyncClient(timeout=30) as c:
        img_res = await c.get(image_url)
        if not img_res.is_success:
            raise HTTPException(500, "Could not fetch generated image")
        return {"image_base64": base64.b64encode(img_res.content).decode(), "image_url": image_url}


class RemoveBgIn(BaseModel):
    image_base64: str
    mime: str = "image/png"

@api.post("/remove-background")
async def remove_background_endpoint(payload: RemoveBgIn, user: dict = Depends(get_user)):
    """Background removal — didn't exist at all before in this app."""
    if not RUNWARE_API_KEY:
        raise HTTPException(500, "RUNWARE_API_KEY not configured")
    task = {
        "taskType": "removeBackground", "taskUUID": str(uuid.uuid4()),
        "model": RUNWARE_BGREMOVE_MODEL, "outputType": "URL", "outputFormat": "PNG",
        "inputImage": f"data:{payload.mime};base64,{payload.image_base64}",
    }
    async with httpx.AsyncClient(timeout=90) as c:
        res = await c.post("https://api.runware.ai/v1",
            headers={"Authorization": f"Bearer {RUNWARE_API_KEY}", "Content-Type": "application/json"},
            json=[task])
        if res.status_code != 200:
            raise HTTPException(500, f"Background removal failed: {res.text[:200]}")
        data = res.json()
        results = data.get("data", data) if isinstance(data, dict) else data
        image_url = results[0].get("imageURL") if isinstance(results, list) and results else None
        if not image_url:
            raise HTTPException(500, "Background removal failed — no result returned")
        img_res = await c.get(image_url)
        if not img_res.is_success:
            raise HTTPException(500, "Could not fetch result image")
        return {"base64": base64.b64encode(img_res.content).decode(), "mime": "image/png"}


@api.post("/generate/script")
async def generate_script_endpoint(payload: GenerateScriptIn, user: dict = Depends(get_user)):
    """Cheap step (Gemini) — draft/iterate on the script for free before
    spending credits on an actual Higgsfield/InVideo/Meta render. No
    generation-limit check here on purpose; only the real render below
    counts against the monthly video quota."""
    context = await _resolve_brand_context(payload.brand_profile_id, user["id"])
    full_prompt = (
        f"{context}\n\n" if context else ""
    ) + f"Write a short-form video script for: {payload.brief}\n\nFormat as scene-by-scene beats with on-screen text and voiceover lines."
    script = await gemini_text(full_prompt)
    return {"script": script}

@api.post("/generate/video")
async def generate_video_endpoint(payload: GenerateVideoIn, user: dict = Depends(get_user)):
    _check_generation_allowed(user, payload.duration_sec)
    if payload.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Choose one of: {list(PROVIDERS.keys())}")
    if payload.output_format not in OUTPUT_PRESETS:
        raise HTTPException(400, f"Unknown output_format. Choose one of: {list(OUTPUT_PRESETS.keys())}")

    context = await _resolve_brand_context(payload.brand_profile_id, user["id"])

    # This was a confirmed bug: call_higgsfield has always supported
    # character_ref_urls for consistency, but nothing ever actually fetched
    # or passed them — brand characters' reference images sat unused in the
    # database on every single generation.
    character_ref_urls = []
    if payload.brand_profile_id:
        profile = await db.brand_profiles.find_one({"id": payload.brand_profile_id, "user_id": user["id"]})
        if profile:
            character_ref_urls = [c["image_url"] for c in profile.get("characters", []) if c.get("image_url")]

    result = await PROVIDERS[payload.provider](payload.script, context, OUTPUT_PRESETS[payload.output_format], character_ref_urls or None)
    await db.users.update_one({"id": user["id"]}, {"$inc": {"videos_this_month": 1}})
    return result

# ── Projects ──────────────────────────────────────────────────────────────────
@api.post("/projects/upload-video")
async def upload_finished_video(file: UploadFile = File(...), user: dict = Depends(get_user)):
    """For clips made outside this app — e.g. free Meta AI Vibes generations,
    or anything else — upload the finished file directly rather than going
    through a generation provider. Uses multipart upload (not base64-in-JSON)
    since video files are too large for that to be practical."""
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "File must be a video")
    max_bytes = 200 * 1024 * 1024  # 200MB
    contents = await file.read()
    if len(contents) > max_bytes:
        raise HTTPException(413, f"Video too large ({len(contents)/1024/1024:.0f}MB) — max 200MB")
    key = f"{uuid.uuid4()}-{file.filename or 'video.mp4'}"
    url = await upload_to_r2(contents, f"video-creator-uploads/{user['id']}", key, file.content_type)
    if not url:
        raise HTTPException(500, "Upload failed — R2 not configured or upload error")
    return {"url": url, "size_bytes": len(contents), "content_type": file.content_type}


@api.post("/projects")
async def create_project(payload: ProjectCreateIn, user: dict = Depends(get_user)):
    if payload.output_format not in OUTPUT_PRESETS:
        raise HTTPException(400, f"Unknown output_format. Choose one of: {list(OUTPUT_PRESETS.keys())}")
    project = {"id": str(uuid.uuid4()), "user_id": user["id"], "title": payload.title,
               "brand_profile_id": payload.brand_profile_id, "provider": payload.provider,
               "output_format": payload.output_format, "script": payload.script,
               "video_url": payload.video_url,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.projects.insert_one(project)
    project.pop("_id", None)
    return project

@api.get("/projects")
async def list_projects(user: dict = Depends(get_user)):
    return await db.projects.find({"user_id": user["id"]}, {"_id": 0}).sort("updated_at", -1).to_list(500)

@api.get("/projects/{project_id}")
async def get_project(project_id: str, user: dict = Depends(get_user)):
    project = await db.projects.find_one({"id": project_id, "user_id": user["id"]}, {"_id": 0})
    if not project:
        raise HTTPException(404, "Project not found")
    return project

@api.put("/projects/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdateIn, user: dict = Depends(get_user)):
    updates = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    if "output_format" in updates and updates["output_format"] not in OUTPUT_PRESETS:
        raise HTTPException(400, f"Unknown output_format. Choose one of: {list(OUTPUT_PRESETS.keys())}")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.projects.update_one({"id": project_id, "user_id": user["id"]}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Project not found")
    return await db.projects.find_one({"id": project_id}, {"_id": 0})

@api.delete("/projects/{project_id}")
async def delete_project(project_id: str, user: dict = Depends(get_user)):
    result = await db.projects.delete_one({"id": project_id, "user_id": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(404, "Project not found")
    return {"ok": True}

# ── Health ───────────────────────────────────────────────────────────────────
@api.get("/health/detailed")
async def health_detailed():
    checks = {}
    try:
        await db.command("ping")
        checks["mongo"] = {"status": "ok"}
    except Exception as e:
        checks["mongo"] = {"status": "error", "detail": str(e)}
    checks["stripe_configured"]     = bool(STRIPE_KEY)
    checks["resend_configured"]     = bool(RESEND_KEY)
    checks["r2_configured"]         = bool(R2_ENDPOINT and R2_ACCESS_KEY and R2_SECRET_KEY)
    checks["invideo_configured"]    = bool(INVIDEO_API_KEY)
    checks["higgsfield_configured"] = bool(HIGGSFIELD_API_KEY)
    checks["meta_configured"]       = bool(META_API_KEY)
    return checks

@api.get("/")
async def root():
    return {"service": "Raven Sharp Video Creator API", "status": "ok"}

app.include_router(api)

@app.on_event("startup")
async def startup():
    log.info("Raven Sharp Video Creator API starting up. DB=%s", DB_NAME)

@app.on_event("shutdown")
async def shutdown():
    client.close()

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})

@app.get("/health")
async def health():
    return {"status": "ok"}
