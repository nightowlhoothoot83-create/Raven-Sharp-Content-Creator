# Raven Sharp Video Creator

AI-generated short-form video with per-user brand profiles (logo, colors,
character reference images, brand bible) and a choice of generation provider.

Part of the Ascension Digital Group / Raven Sharp SaaS suite — same
architecture pattern as Raven Sharp Book Creator / Image Optimiser / POD:

- **Frontend**: static single-file app (`index.html`), Cloudflare Pages
- **Backend**: FastAPI on Railway (`backend/server.py`)
- **Auth**: JWT (access + refresh cookies), bcrypt password hashing
- **Billing**: Stripe subscriptions (tiers: free / creator / studio)
- **Storage**: MongoDB Atlas (motor) + Cloudflare R2 for brand assets

## Video generation providers — status as of this build

| Provider | Status |
|---|---|
| **Higgsfield** | Real integration, via the official `higgsfield-client` PyPI package (verified to exist, auth pattern confirmed). Needs `HF_API_KEY`+`HF_API_SECRET` from cloud.higgsfield.ai, and `HIGGSFIELD_VIDEO_MODEL` confirmed against your dashboard's current model catalog. |
| **InVideo.ai** | A real API exists (`pro-api.invideo.io`), but the only docs found during this build were 2023-era ChatGPT-plugin manifests — almost certainly stale. Get current docs + API key from your invideo.io dashboard (Settings → Developers → API Keys) before filling in `call_invideo()` in `server.py`. |
| **Meta AI** | No verified public API found. Stub only — needs research once you have a confirmed source. |

## Status

🚧 Backend built and tested (import + non-DB endpoints verified; Higgsfield's
SDK dependency confirmed installable). Not yet deployed.

Outstanding before going live:
- [ ] Create Railway service, set env vars (see `backend/.env.example`)
- [ ] Create MongoDB Atlas database
- [ ] Create Stripe products/prices for `creator`/`studio` tiers, replace
      placeholder price IDs in `backend/server.py` (`STRIPE_PRICES`)
- [ ] Set up Stripe webhook endpoint at `/api/billing/webhook`
- [ ] Create `video.raven-sharp.com` subdomain + Cloudflare Pages project
- [ ] Confirm Higgsfield's video model id, fill in `HIGGSFIELD_VIDEO_MODEL`
- [ ] Get current InVideo API docs and fill in `call_invideo()`
- [ ] Research Meta AI video API or drop that provider option
- [ ] Wire frontend (`index.html`) to call this backend instead of Higgsfield
      prompt-copy workflow

## Local dev

```
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in real values
uvicorn server:app --reload
```
