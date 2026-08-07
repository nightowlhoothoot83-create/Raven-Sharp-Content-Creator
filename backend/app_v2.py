"""Raven Sharp Content / Video Creator v2 production extensions.

This module turns the existing brand-profile backend into an output engine:
- imports one-time Book Creator handoffs
- renders finished brand-aligned posts, memes, polls and carousel PNG packs
- animates selected source images through Runware Vidu 2.0 economy mode
- creates narration through Runware xAI TTS
- composes generated clips and zero-cost pan/zoom stills into a final MP4 with exact text overlays

The legacy API remains intact; this module adds /api/v2 routes only.
"""
from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import textwrap
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont, ImageOps

import server as legacy

app = legacy.app
v2 = APIRouter(prefix="/api/v2", tags=["production-v2"])

RUNWARE_API = "https://api.runware.ai/v1"
BOOK_CREATOR_API_BASE = os.environ.get(
    "BOOK_CREATOR_API_BASE", "https://web-production-912b2.up.railway.app/api"
).rstrip("/")

FORMAT_DIMS = {
    "post": (1080, 1080),
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "reel": (1080, 1920),
    "short": (1080, 1920),
    "landscape": (1920, 1080),
}
VIDEO_720_DIMS = {
    "vertical": (720, 1280),
    "square": (720, 720),
    "horizontal": (1280, 720),
}


class ImportBookHandoffIn(BaseModel):
    token: str


class SocialImageIn(BaseModel):
    brand_profile_id: Optional[str] = None
    kind: str = "post"  # post | meme | poll | quote | cover
    format: str = "post"
    headline: str
    body: str = ""
    footer: str = ""
    options: List[str] = Field(default_factory=list)
    background_image_url: Optional[str] = None
    filename: Optional[str] = None


class CarouselSlide(BaseModel):
    headline: str
    body: str = ""
    footer: str = ""
    background_image_url: Optional[str] = None


class CarouselIn(BaseModel):
    brand_profile_id: Optional[str] = None
    format: str = "portrait"
    title: str = "carousel"
    slides: List[CarouselSlide]


class AnimateFrameIn(BaseModel):
    brand_profile_id: Optional[str] = None
    image_url: str
    next_image_url: Optional[str] = None
    prompt: str = "Subtle natural motion, preserve the original character design and composition."
    aspect: str = "vertical"  # vertical | square | horizontal
    movement: str = "small"  # auto | small | medium | large


class NarrationIn(BaseModel):
    text: str
    voice: str = "eve"


class ComposeScene(BaseModel):
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    caption: str = ""
    duration: float = 4.0


class ComposeVideoIn(BaseModel):
    brand_profile_id: Optional[str] = None
    title: str = "raven-sharp-video"
    aspect: str = "vertical"
    scenes: List[ComposeScene]
    narration_url: Optional[str] = None


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _hex(value: Optional[str], fallback: str) -> str:
    value = (value or "").strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            pass
    return fallback


async def _profile(profile_id: Optional[str], user_id: str) -> dict:
    if not profile_id:
        return {}
    return await legacy.db.brand_profiles.find_one(
        {"id": profile_id, "user_id": user_id}, {"_id": 0}
    ) or {}


def _safe_remote_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(400, "Only HTTPS asset URLs are accepted")
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise HTTPException(400, "Local asset URLs are not allowed")
    try:
        for addr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(addr[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(400, "Private-network asset URLs are not allowed")
    except socket.gaierror:
        raise HTTPException(400, "Asset host could not be resolved")
    return url


async def _download(url: str, max_bytes: int = 30 * 1024 * 1024) -> bytes:
    _safe_remote_url(url)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(url)
        if not response.is_success:
            raise HTTPException(502, f"Could not fetch asset ({response.status_code})")
        data = response.content
    if len(data) > max_bytes:
        raise HTTPException(413, "Remote asset is too large")
    return data


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if not text:
        return ""
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


async def _brand_canvas(
    profile: dict,
    width: int,
    height: int,
    headline: str,
    body: str,
    footer: str,
    options: List[str],
    background_image_url: Optional[str],
) -> bytes:
    primary = _hex(profile.get("primary_color"), "#7c5cbf")
    secondary = _hex(profile.get("secondary_color"), "#38bdf8")
    canvas = Image.new("RGB", (width, height), primary)

    if background_image_url:
        raw = await _download(background_image_url)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
        canvas.paste(image)
        shade = Image.new("RGBA", (width, height), (0, 0, 0, 105))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), shade).convert("RGB")

    draw = ImageDraw.Draw(canvas)
    pad = max(48, int(width * 0.065))
    headline_font = _font(max(42, int(width * 0.065)), bold=True)
    body_font = _font(max(25, int(width * 0.031)))
    option_font = _font(max(24, int(width * 0.029)), bold=True)
    footer_font = _font(max(20, int(width * 0.023)))

    y = pad
    wrapped_headline = _wrap(draw, headline, headline_font, width - pad * 2)
    draw.multiline_text((pad, y), wrapped_headline, font=headline_font, fill="white", spacing=10)
    hb = draw.multiline_textbbox((pad, y), wrapped_headline, font=headline_font, spacing=10)
    y = hb[3] + int(height * 0.035)

    if body:
        wrapped_body = _wrap(draw, body, body_font, width - pad * 2)
        draw.multiline_text((pad, y), wrapped_body, font=body_font, fill=(245, 245, 255), spacing=9)
        bb = draw.multiline_textbbox((pad, y), wrapped_body, font=body_font, spacing=9)
        y = bb[3] + int(height * 0.035)

    for option in options[:6]:
        box_h = max(68, int(height * 0.06))
        draw.rounded_rectangle((pad, y, width - pad, y + box_h), radius=18, fill=secondary, outline="white", width=2)
        label = _wrap(draw, option, option_font, width - pad * 2 - 40)
        draw.text((pad + 20, y + (box_h - option_font.size) / 2 - 3), label, font=option_font, fill="white")
        y += box_h + 16

    brand_name = profile.get("name") or "Raven Sharp"
    footer_text = footer.strip() or brand_name
    draw.text((pad, height - pad), footer_text, font=footer_font, fill=(240, 240, 255), anchor="ls")

    logo_url = profile.get("logo_url")
    if logo_url:
        try:
            logo_raw = await _download(logo_url, 8 * 1024 * 1024)
            logo = Image.open(io.BytesIO(logo_raw)).convert("RGBA")
            logo.thumbnail((int(width * 0.18), int(height * 0.1)), Image.Resampling.LANCZOS)
            canvas.paste(logo, (width - pad - logo.width, height - pad - logo.height), logo)
        except Exception:
            legacy.log.exception("Logo rendering failed; continuing without logo")

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def _store_bytes(data: bytes, prefix: str, filename: str, mime: str) -> dict:
    url = ""
    try:
        url = await legacy.upload_to_r2(data, prefix, filename, mime)
    except Exception:
        legacy.log.exception("R2 output upload failed")
    return {
        "url": url,
        "base64": None if url else base64.b64encode(data).decode(),
        "mime": mime,
        "filename": filename,
        "bytes": len(data),
    }


@v2.post("/import/book-handoff")
async def import_book_handoff(payload: ImportBookHandoffIn, user: dict = Depends(legacy.get_user)):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{BOOK_CREATOR_API_BASE}/v2/handoffs/{payload.token}")
    if response.status_code >= 400:
        detail = response.json().get("detail", "Book handoff could not be redeemed") if response.headers.get("content-type", "").startswith("application/json") else "Book handoff could not be redeemed"
        raise HTTPException(response.status_code, detail)
    bundle = response.json()

    brand = bundle.get("brand") or {}
    brand_profile_id = None
    if brand.get("name") or brand.get("brand_bible"):
        external_key = f"book-handoff:{bundle.get('book_id')}"
        existing = await legacy.db.brand_profiles.find_one({"user_id": user["id"], "external_key": external_key})
        brand_profile_id = existing.get("id") if existing else str(uuid.uuid4())
        await legacy.db.brand_profiles.update_one(
            {"user_id": user["id"], "external_key": external_key},
            {"$set": {
                "id": brand_profile_id,
                "user_id": user["id"],
                "external_key": external_key,
                "name": brand.get("name") or bundle.get("title") or "Imported Book Brand",
                "brand_bible": brand.get("brand_bible", ""),
                "primary_color": brand.get("primary_color"),
                "secondary_color": brand.get("secondary_color"),
                "logo_url": brand.get("logo_url"),
                "characters": brand.get("characters", []),
                "assets": [
                    {"name": f"Book page {i + 1}", "url": url, "type": "reference", "description": "Imported from Book Creator"}
                    for i, url in enumerate(bundle.get("reference_images", []))
                ],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    project_id = str(uuid.uuid4())
    scenes = [
        {
            "page": p.get("page"),
            "voiceover": p.get("text", ""),
            "image_url": p.get("image_url"),
            "duration": 4,
            "animation": "still_pan_zoom",
        }
        for p in bundle.get("pages", [])
    ]
    project = {
        "id": project_id,
        "user_id": user["id"],
        "source": "book_creator",
        "source_book_id": bundle.get("book_id"),
        "title": bundle.get("suggested_project_title") or f"{bundle.get('title', 'Book')} — Video",
        "brand_profile_id": brand_profile_id,
        "scenes": scenes,
        "status": "imported",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await legacy.db.projects.insert_one(project)
    project.pop("_id", None)
    return {"ok": True, "project": project, "economy_note": "Pages default to zero-AI pan/zoom. Upgrade only selected scenes to AI animation."}


@v2.post("/render/social-image")
async def render_social_image(payload: SocialImageIn, user: dict = Depends(legacy.get_user)):
    if payload.format not in FORMAT_DIMS:
        raise HTTPException(400, f"Unknown format. Choose one of: {list(FORMAT_DIMS)}")
    profile = await _profile(payload.brand_profile_id, user["id"])
    width, height = FORMAT_DIMS[payload.format]
    png = await _brand_canvas(profile, width, height, payload.headline, payload.body, payload.footer, payload.options, payload.background_image_url)
    filename = payload.filename or f"{payload.kind}-{uuid.uuid4().hex[:8]}.png"
    return await _store_bytes(png, f"content-production/{user['id']}/static", filename, "image/png")


@v2.post("/render/carousel")
async def render_carousel(payload: CarouselIn, user: dict = Depends(legacy.get_user)):
    if not payload.slides or len(payload.slides) > 20:
        raise HTTPException(400, "Carousel must contain 1 to 20 slides")
    if payload.format not in FORMAT_DIMS:
        raise HTTPException(400, f"Unknown format. Choose one of: {list(FORMAT_DIMS)}")
    profile = await _profile(payload.brand_profile_id, user["id"])
    width, height = FORMAT_DIMS[payload.format]
    outputs = []
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, slide in enumerate(payload.slides, start=1):
            png = await _brand_canvas(profile, width, height, slide.headline, slide.body, slide.footer, [], slide.background_image_url)
            filename = f"slide-{index:02d}.png"
            zf.writestr(filename, png)
            stored = await _store_bytes(png, f"content-production/{user['id']}/carousels/{uuid.uuid4().hex[:8]}", filename, "image/png")
            outputs.append(stored)
        zf.writestr("posting-copy.json", json.dumps({"title": payload.title, "slides": [s.model_dump() for s in payload.slides]}, indent=2))
    zip_data = zip_buf.getvalue()
    zip_output = await _store_bytes(zip_data, f"content-production/{user['id']}/carousels", f"{payload.title.replace(' ', '-')[:60]}.zip", "application/zip")
    return {"slides": outputs, "zip": zip_output}


async def _runware(tasks: list) -> dict:
    if not legacy.RUNWARE_API_KEY:
        raise HTTPException(503, "RUNWARE_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            RUNWARE_API,
            headers={"Authorization": f"Bearer {legacy.RUNWARE_API_KEY}", "Content-Type": "application/json"},
            json=tasks,
        )
    if response.status_code >= 400:
        raise HTTPException(502, f"Runware error {response.status_code}: {response.text[:400]}")
    data = response.json()
    if data.get("errors") and not data.get("data"):
        raise HTTPException(502, data["errors"][0].get("message", "Runware request failed"))
    return data


@v2.post("/video/animate-frame")
async def animate_frame(payload: AnimateFrameIn, user: dict = Depends(legacy.get_user)):
    if payload.aspect not in VIDEO_720_DIMS:
        raise HTTPException(400, "aspect must be vertical, square or horizontal")
    if payload.movement not in {"auto", "small", "medium", "large"}:
        raise HTTPException(400, "movement must be auto, small, medium or large")
    width, height = VIDEO_720_DIMS[payload.aspect]
    task_uuid = str(uuid.uuid4())
    frames = [_safe_remote_url(payload.image_url)]
    if payload.next_image_url:
        frames.append(_safe_remote_url(payload.next_image_url))
    profile = await _profile(payload.brand_profile_id, user["id"])
    context = await legacy._resolve_brand_context(payload.brand_profile_id, user["id"])
    prompt = f"{context}\n\n{payload.prompt}".strip() if context else payload.prompt
    task = {
        "taskType": "videoInference",
        "taskUUID": task_uuid,
        "model": "vidu:2@0",
        "positivePrompt": prompt[:3000],
        "width": width,
        "height": height,
        "duration": 4,
        "inputs": {"frameImages": frames},
        "providerSettings": {"vidu": {"movementAmplitude": payload.movement, "bgm": False}},
        "outputFormat": "MP4",
        "outputType": "URL",
        "deliveryMethod": "async",
        "includeCost": True,
    }
    response = await _runware([task])
    await legacy.db.production_jobs.insert_one({
        "task_uuid": task_uuid,
        "user_id": user["id"],
        "kind": "video_animation",
        "model": "vidu:2@0",
        "status": "processing",
        "request": {"aspect": payload.aspect, "movement": payload.movement, "image_url": payload.image_url},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"task_uuid": task_uuid, "status": "processing", "model": "vidu:2@0", "provider_response": response}


@v2.get("/video/jobs/{task_uuid}")
async def video_job_status(task_uuid: str, user: dict = Depends(legacy.get_user)):
    job = await legacy.db.production_jobs.find_one({"task_uuid": task_uuid, "user_id": user["id"]}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Video job not found")
    response = await _runware([{"taskType": "getResponse", "taskUUID": task_uuid}])
    items = response.get("data", [])
    errors = response.get("errors", [])
    successful = next((item for item in items if item.get("status") == "success" or item.get("videoURL")), None)
    processing = next((item for item in items if item.get("status") == "processing"), None)
    if successful:
        await legacy.db.production_jobs.update_one({"task_uuid": task_uuid}, {"$set": {"status": "success", "result": successful, "completed_at": datetime.now(timezone.utc).isoformat()}})
        return {"status": "success", **successful}
    if errors:
        await legacy.db.production_jobs.update_one({"task_uuid": task_uuid}, {"$set": {"status": "error", "errors": errors}})
        return {"status": "error", "errors": errors}
    return {"status": "processing", "progress": (processing or {}).get("progress"), "data": items}


@v2.post("/audio/narration")
async def create_narration(payload: NarrationIn, user: dict = Depends(legacy.get_user)):
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "Narration text is empty")
    if len(text) > 10_000:
        raise HTTPException(413, "Narration is too long for one request")
    task_uuid = str(uuid.uuid4())
    response = await _runware([{
        "taskType": "audioInference",
        "taskUUID": task_uuid,
        "model": "xai:tts@0",
        "speech": {"text": text, "voice": payload.voice, "language": "en"},
        "outputType": "URL",
        "outputFormat": "MP3",
        "deliveryMethod": "sync",
        "includeCost": True,
    }])
    item = next((x for x in response.get("data", []) if x.get("audioURL")), None)
    if not item:
        raise HTTPException(502, "Narration generation returned no audio")
    audio = await _download(item["audioURL"], 50 * 1024 * 1024)
    stored = await _store_bytes(audio, f"content-production/{user['id']}/audio", f"narration-{task_uuid[:8]}.mp3", "audio/mpeg")
    return {**stored, "cost": item.get("cost"), "voice": payload.voice}


def _caption_overlay(width: int, height: int, caption: str, profile: dict, path: Path):
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not caption.strip():
        overlay.save(path)
        return
    draw = ImageDraw.Draw(overlay)
    font = _font(max(26, int(width * 0.045)), bold=True)
    max_width = int(width * 0.82)
    wrapped = _wrap(draw, caption, font, max_width)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=7, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = width // 2
    y = int(height * 0.78)
    pad_x, pad_y = 28, 18
    draw.rounded_rectangle((x - tw/2 - pad_x, y - pad_y, x + tw/2 + pad_x, y + th + pad_y), radius=20, fill=(0, 0, 0, 175))
    draw.multiline_text((x, y), wrapped, font=font, fill="white", anchor="ma", align="center", spacing=7, stroke_width=2, stroke_fill="black")
    overlay.save(path)


async def _compose_job(job_id: str, payload: ComposeVideoIn, user_id: str):
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        width, height = VIDEO_720_DIMS.get(payload.aspect, VIDEO_720_DIMS["vertical"])
        profile = await _profile(payload.brand_profile_id, user_id)
        with tempfile.TemporaryDirectory(prefix="raven-video-") as td:
            tmp = Path(td)
            rendered = []
            for i, scene in enumerate(payload.scenes):
                if not scene.video_url and not scene.image_url:
                    continue
                caption_path = tmp / f"caption-{i}.png"
                _caption_overlay(width, height, scene.caption, profile, caption_path)
                clip_path = tmp / f"clip-{i}.mp4"
                duration = max(1.0, min(float(scene.duration), 20.0))
                if scene.video_url:
                    source = tmp / f"source-{i}.mp4"
                    source.write_bytes(await _download(scene.video_url, 120 * 1024 * 1024))
                    cmd = [
                        ffmpeg, "-y", "-i", str(source), "-i", str(caption_path),
                        "-filter_complex",
                        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p[base];[base][1:v]overlay=0:0[v]",
                        "-map", "[v]", "-an", "-t", str(duration), "-r", "30", "-c:v", "libx264", "-preset", "veryfast", str(clip_path),
                    ]
                else:
                    source = tmp / f"source-{i}.img"
                    source.write_bytes(await _download(scene.image_url, 30 * 1024 * 1024))
                    frames = max(30, int(duration * 30))
                    cmd = [
                        ffmpeg, "-y", "-loop", "1", "-i", str(source), "-i", str(caption_path),
                        "-filter_complex",
                        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},zoompan=z='min(zoom+0.0007,1.08)':d={frames}:s={width}x{height}:fps=30,format=yuv420p[base];[base][1:v]overlay=0:0[v]",
                        "-map", "[v]", "-an", "-t", str(duration), "-r", "30", "-c:v", "libx264", "-preset", "veryfast", str(clip_path),
                    ]
                await asyncio.to_thread(subprocess.run, cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
                rendered.append(clip_path)

            if not rendered:
                raise RuntimeError("No renderable scenes were supplied")
            concat_file = tmp / "concat.txt"
            concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in rendered), encoding="utf-8")
            silent = tmp / "silent.mp4"
            await asyncio.to_thread(subprocess.run, [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            final = tmp / "final.mp4"
            if payload.narration_url:
                narration = tmp / "narration.mp3"
                narration.write_bytes(await _download(payload.narration_url, 60 * 1024 * 1024))
                cmd = [ffmpeg, "-y", "-i", str(silent), "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(final)]
                await asyncio.to_thread(subprocess.run, cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            else:
                final.write_bytes(silent.read_bytes())
            data = final.read_bytes()
            stored = await _store_bytes(data, f"content-production/{user_id}/videos", f"{payload.title.replace(' ', '-')[:60]}-{job_id[:8]}.mp4", "video/mp4")
            await legacy.db.production_jobs.update_one({"job_id": job_id}, {"$set": {"status": "success", "result": stored, "completed_at": datetime.now(timezone.utc).isoformat()}})
    except Exception as exc:
        legacy.log.exception("Video composition failed")
        await legacy.db.production_jobs.update_one({"job_id": job_id}, {"$set": {"status": "error", "error": str(exc), "completed_at": datetime.now(timezone.utc).isoformat()}})


@v2.post("/video/compose")
async def compose_video(payload: ComposeVideoIn, background_tasks: BackgroundTasks, user: dict = Depends(legacy.get_user)):
    if not payload.scenes or len(payload.scenes) > 60:
        raise HTTPException(400, "Video must contain 1 to 60 scenes")
    if payload.aspect not in VIDEO_720_DIMS:
        raise HTTPException(400, "aspect must be vertical, square or horizontal")
    job_id = str(uuid.uuid4())
    await legacy.db.production_jobs.insert_one({
        "job_id": job_id,
        "user_id": user["id"],
        "kind": "video_composition",
        "status": "processing",
        "title": payload.title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    background_tasks.add_task(_compose_job, job_id, payload, user["id"])
    return {"job_id": job_id, "status": "processing"}


@v2.get("/production/jobs/{job_id}")
async def production_job(job_id: str, user: dict = Depends(legacy.get_user)):
    job = await legacy.db.production_jobs.find_one({"job_id": job_id, "user_id": user["id"]}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Production job not found")
    return job


@v2.get("/capabilities")
async def production_capabilities():
    return {
        "brand_profiles": True,
        "finished_static_outputs": ["post", "meme", "poll", "quote", "carousel"],
        "finished_video_outputs": ["reel", "short", "vertical", "square", "horizontal"],
        "book_creator_handoff": True,
        "economy_video": {"still_motion": "ffmpeg pan/zoom", "ai_animation": "vidu:2@0", "clip_seconds": 4},
        "narration": "xai:tts@0",
        "exact_text_overlays": True,
    }


app.include_router(v2)
