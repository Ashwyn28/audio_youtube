"""Bounded, range-aware audio transport for the browser's native media player.

Only canonical YouTube IDs enter the resolver. Extracted media URLs are restricted
to HTTPS Google Video hosts, never supplied by a client or followed via redirects.
No audio is downloaded to disk or buffered in its entirety.
"""

import asyncio
import importlib.util
import logging
import os
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import anyio
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

router = APIRouter(prefix="/audio", tags=["playback"])
logger = logging.getLogger(__name__)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
BYTE_RANGE = re.compile(r"^bytes=(?:\d+-\d*|-\d+)$")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-resolver")
_cache = OrderedDict()
_inflight = {}
_active_streams = 0
MAX_STREAMS = 12
MAX_RESOLVERS = 8
CACHE_SIZE = 128
RESOLVE_TIMEOUT = 35


@dataclass(frozen=True)
class AudioSource:
    url: str
    headers: dict
    expires: float


class AudioResponse(StreamingResponse):
    """Release upstream resources even if sending response headers fails."""

    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self._cleanup()


def audio_enabled():
    return os.getenv("AUDIO_ENABLED", "true").lower() in {"true", "1", "yes"}


def validate_source(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except (ValueError, TypeError):
        raise HTTPException(502, "The audio provider returned an unsupported stream.") from None
    if (
        parts.scheme != "https"
        or not host.endswith(".googlevideo.com")
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise HTTPException(502, "The audio provider returned an unsupported stream.")


def _extract(video_id):
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise HTTPException(503, "Audio playback is not installed on this server.") from exc

    class QuietLogger:
        def debug(self, message):
            pass

        info = debug
        warning = debug
        error = debug

    options = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "skip_download": True,
        "cachedir": False,
        "socket_timeout": 10,
        "retries": 1,
        "extractor_retries": 1,
        "js_runtimes": {"node": {}},
    }
    try:
        with YoutubeDL(options) as resolver:
            info = resolver.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
        url = info.get("url", "")
        validate_source(url)
        # Never forward cookies, authorization, or arbitrary extractor headers.
        headers = {
            key: value
            for key, value in info.get("http_headers", {}).items()
            if key.lower() in {"user-agent", "referer", "origin"}
        }
        return AudioSource(url, headers, time.monotonic() + 120)
    except HTTPException:
        raise
    except Exception as exc:
        # Extractor exceptions may contain signed URLs; do not log their text.
        logger.warning("Audio resolution failed (%s)", type(exc).__name__)
        raise HTTPException(
            502, "This item could not be prepared for audio playback. Retry or choose another item."
        ) from exc


async def _resolve_and_cache(video_id):
    loop = asyncio.get_running_loop()
    # The fixed executor bounds actual extraction even after a request times out.
    source = await loop.run_in_executor(_executor, _extract, video_id)
    _cache[video_id] = source
    _cache.move_to_end(video_id)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return source


async def resolve_audio(video_id):
    cached = _cache.get(video_id)
    if cached and cached.expires > time.monotonic():
        _cache.move_to_end(video_id)
        return cached
    task = _inflight.get(video_id)
    if task is None:
        if len(_inflight) >= MAX_RESOLVERS:
            raise HTTPException(503, "Audio preparation is busy. Please retry shortly.")
        task = asyncio.create_task(_resolve_and_cache(video_id))
        _inflight[video_id] = task

        def completed(future):
            _inflight.pop(video_id, None)
            # Retrieve failures even if every waiting request was disconnected.
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=RESOLVE_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "Preparing audio took too long. Please retry.") from exc


@router.get("/status")
async def audio_status():
    return {
        "enabled": audio_enabled(),
        "installed": importlib.util.find_spec("yt_dlp") is not None,
        "transport": "native-audio",
    }


@router.get("/{video_id}")
async def stream_audio(video_id: str, request: Request):
    global _active_streams
    if not VIDEO_ID.fullmatch(video_id):
        raise HTTPException(422, "A valid YouTube video ID is required.")
    if not audio_enabled():
        raise HTTPException(503, "Audio playback is disabled on this server.")
    byte_range = request.headers.get("range")
    if byte_range and not BYTE_RANGE.fullmatch(byte_range):
        raise HTTPException(416, "Only a single byte range is supported.")
    if _active_streams >= MAX_STREAMS:
        raise HTTPException(503, "All audio connections are busy. Please retry shortly.")
    _active_streams += 1
    client = None
    upstream = None
    closed = False

    async def close():
        nonlocal closed
        global _active_streams
        if closed:
            return
        # Starlette may cancel its streaming task on disconnect. Shield both
        # closing operations so cancellation cannot leave a half-closed client.
        with anyio.CancelScope(shield=True):
            closed = True
            try:
                if upstream is not None:
                    await upstream.aclose()
            finally:
                try:
                    if client is not None:
                        await client.aclose()
                finally:
                    _active_streams -= 1

    try:
        source = await resolve_audio(video_id)
        validate_source(source.url)
        headers = {**source.headers, "Accept-Encoding": "identity"}
        if byte_range:
            headers["Range"] = byte_range
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False
        )
        for attempt in range(2):
            upstream = await client.send(
                client.build_request("GET", source.url, headers=headers), stream=True
            )
            if upstream.status_code not in {403, 410} or attempt:
                break
            await upstream.aclose()
            _cache.pop(video_id, None)
            source = await resolve_audio(video_id)
            validate_source(source.url)
            headers = {**source.headers, "Accept-Encoding": "identity"}
            if byte_range:
                headers["Range"] = byte_range
        if upstream.status_code not in {200, 206, 416}:
            raise HTTPException(502, "The audio stream is unavailable. Please retry.")
        if upstream.status_code == 416:
            raise HTTPException(
                416,
                "The requested playback position is unavailable.",
                headers={"Content-Range": upstream.headers.get("content-range", "bytes */*")},
            )
        content_type = upstream.headers.get("content-type", "audio/mp4").split(";")[0]
        if not (content_type.startswith("audio/") or content_type in {"video/mp4", "application/octet-stream"}):
            raise HTTPException(502, "The provider did not return playable audio.")
        response_headers = {
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        for key in ("content-length", "content-range", "accept-ranges"):
            if key in upstream.headers:
                response_headers[key] = upstream.headers[key]

        async def chunks():
            try:
                async for chunk in upstream.aiter_raw(64 * 1024):
                    yield chunk
            except httpx.HTTPError as exc:
                # ASGI logs exceptions raised after response headers are sent;
                # replace upstream errors rather than exposing signed URLs.
                logger.warning("Audio stream interrupted (%s)", type(exc).__name__)
                raise RuntimeError("The audio connection was interrupted.") from None
            finally:
                await close()

        return AudioResponse(
            chunks(),
            status_code=upstream.status_code,
            media_type=content_type,
            headers=response_headers,
            cleanup=close,
        )
    except BaseException as exc:
        await close()
        if isinstance(exc, httpx.HTTPError):
            raise HTTPException(502, "The audio connection failed. Please retry.") from exc
        raise
