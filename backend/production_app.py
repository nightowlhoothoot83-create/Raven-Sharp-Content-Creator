"""Production entrypoint for Raven Sharp Content Creator.

Loads the v2 production engine and upgrades the legacy /api/generate/script route
only when the caller explicitly requests strict JSON. Normal legacy script
requests keep their existing behavior.
"""
import json

from fastapi import APIRouter, Depends

import app_v2

legacy = app_v2.legacy
app = app_v2.app

# Preserve the original endpoint so the old frontend keeps working unchanged.
_original_generate_script = legacy.generate_script_endpoint

# Remove the registered legacy POST route before registering the compatible
# wrapper below. Other /api routes are untouched.
app.router.routes[:] = [
    route for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/api/generate/script"
        and "POST" in (getattr(route, "methods", None) or set())
    )
]

compat = APIRouter(prefix="/api", tags=["generation"])


def _extract_json(text: str):
    value = (text or "").strip()
    if value.startswith("```"):
        value = value.replace("```json", "", 1).replace("```", "", 1).strip()
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        value = value[start:end + 1]
    return json.loads(value)


@compat.post("/generate/script")
async def generate_script_compatible(
    payload: legacy.GenerateScriptIn,
    user: dict = Depends(legacy.get_user),
):
    # studio-v2 marks structured production requests explicitly. Everything
    # else delegates to the original scene-by-scene video-script endpoint.
    if "Return STRICT JSON ONLY" not in payload.brief:
        return await _original_generate_script(payload, user)

    context = await legacy._resolve_brand_context(payload.brand_profile_id, user["id"])
    full_prompt = (
        (context + "\n\n" if context else "")
        + payload.brief
        + "\nThe JSON must parse with JSON.parse exactly. Do not add commentary before or after it."
    )
    raw = await legacy.gemini_text(full_prompt)
    try:
        _extract_json(raw)
        return {"script": raw}
    except Exception:
        repair_prompt = (
            "Repair the following response into valid JSON only. Preserve its meaning and the requested schema. "
            "Do not add markdown or commentary.\n\n" + raw
        )
        repaired = await legacy.gemini_text(repair_prompt)
        # Deliberately let a final parse error surface as a 500 rather than
        # passing malformed content into the renderer.
        _extract_json(repaired)
        return {"script": repaired}


app.include_router(compat)
