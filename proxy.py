"""
Watermark Remover Proxy
Sits between your app and webai-2api.
- Passes all requests through transparently
- When a response contains base64 image data, strips the Gemini watermark
- Supports both streaming and non-streaming responses
"""

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("watermark-proxy")

UPSTREAM_URL     = os.environ["UPSTREAM_URL"].rstrip("/")
UPSTREAM_AUTH    = os.environ.get("UPSTREAM_AUTH", "")
WATERMARK_BINARY = Path(os.environ.get("WATERMARK_BINARY", "/usr/local/bin/GeminiWatermarkTool"))

app = FastAPI(title="Watermark Remover Proxy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Watermark removal
# ---------------------------------------------------------------------------

def remove_watermark_bytes(image_bytes: bytes, suffix: str = ".png") -> bytes:
    if not WATERMARK_BINARY.exists():
        log.warning("WatermarkTool not found, skipping removal")
        return image_bytes
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [str(WATERMARK_BINARY), "--quiet", str(tmp_path)],
            capture_output=True, timeout=30,
        )
        return tmp_path.read_bytes()
    except Exception as e:
        log.error("Watermark removal failed: %s", e)
        return image_bytes
    finally:
        tmp_path.unlink(missing_ok=True)


def process_image_b64(b64: str, mime: str = "image/png") -> str:
    """Decode, remove watermark, re-encode as base64."""
    try:
        raw = base64.b64decode(b64)
        suffix = ".jpg" if "jpeg" in mime else ".png"
        clean = remove_watermark_bytes(raw, suffix)
        return base64.b64encode(clean).decode()
    except Exception as e:
        log.error("process_image_b64 failed: %s", e)
        return b64


def process_response_body(body: bytes) -> bytes:
    """
    Parse JSON response body and strip watermarks from any inline image data.
    Handles both OpenAI-style and Gemini-style response formats.
    """
    try:
        data = json.loads(body)
    except Exception:
        return body  # not JSON, return as-is

    modified = False

    # OpenAI format: choices[].message.content (array of parts)
    for choice in data.get("choices", []):
        content = choice.get("message", {}).get("content", [])
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        mime, b64 = url.split(",", 1) if "," in url else ("data:image/png;base64", url)
                        mime_type = mime.split(":")[1].split(";")[0] if ":" in mime else "image/png"
                        clean_b64 = process_image_b64(b64, mime_type)
                        part["image_url"]["url"] = f"data:{mime_type};base64,{clean_b64}"
                        modified = True

    # Gemini format: candidates[].content.parts[].inlineData
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "inlineData" in part:
                inline = part["inlineData"]
                mime = inline.get("mimeType", "image/png")
                b64  = inline.get("data", "")
                if b64:
                    inline["data"] = process_image_b64(b64, mime)
                    modified = True

    if modified:
        log.info("Watermark removed from response image(s)")
        return json.dumps(data).encode()
    return body


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

SKIP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


def build_upstream_headers(request: Request) -> dict:
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in SKIP_HEADERS
    }
    # Inject upstream auth if caller didn't provide one
    if UPSTREAM_AUTH and "authorization" not in {h.lower() for h in headers}:
        headers["Authorization"] = f"Bearer {UPSTREAM_AUTH}"
    return headers


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "upstream": UPSTREAM_URL,
        "watermark_binary": WATERMARK_BINARY.exists(),
    }


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(request: Request, path: str):
    url     = f"{UPSTREAM_URL}/{path}"
    headers = build_upstream_headers(request)
    body    = await request.body()

    async with httpx.AsyncClient(timeout=120) as client:
        upstream = await client.request(
            method  = request.method,
            url     = url,
            headers = headers,
            content = body,
            params  = dict(request.query_params),
        )

    content_type = upstream.headers.get("content-type", "")

    # Stream responses — return as-is (watermark removal not possible mid-stream)
    if "text/event-stream" in content_type:
        async def stream_gen():
            async for chunk in upstream.aiter_bytes():
                yield chunk
        return StreamingResponse(
            stream_gen(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers={
                k: v for k, v in upstream.headers.items()
                if k.lower() not in SKIP_HEADERS
            },
        )

    # Non-stream JSON — process for watermarks
    response_body = upstream.content
    if "application/json" in content_type:
        response_body = await asyncio.get_event_loop().run_in_executor(
            None, process_response_body, response_body
        )

    return Response(
        content     = response_body,
        status_code = upstream.status_code,
        media_type  = content_type,
        headers     = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "connection"}
        },
    )
