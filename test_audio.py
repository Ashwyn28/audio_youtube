import asyncio
import time
from collections import OrderedDict
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

import audio


@pytest.fixture(autouse=True)
def isolated_audio_state(monkeypatch):
    monkeypatch.setattr(audio, "_cache", OrderedDict())
    monkeypatch.setattr(audio, "_inflight", {})
    monkeypatch.setattr(audio, "_active_streams", 0)


class BytesStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"audio-bytes"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(audio.router)
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    monkeypatch.setattr(audio, "_active_streams", 0)
    audio._cache.clear()
    audio._inflight.clear()
    with TestClient(app) as instance:
        yield instance


def source():
    return audio.AudioSource(
        "https://rr1---example.googlevideo.com/videoplayback?secret=value",
        {"User-Agent": "test"},
        time.monotonic() + 60,
    )


def mock_transport(monkeypatch, status=206):
    requests = []
    closed = []

    async def resolve(video_id):
        return source()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        def build_request(self, method, url, headers):
            request = httpx.Request(method, url, headers=headers)
            requests.append(request)
            return request

        async def send(self, request, stream):
            assert stream
            return httpx.Response(
                status,
                headers={
                    "content-type": "audio/mp4",
                    "content-range": "bytes 0-10/100",
                    "accept-ranges": "bytes",
                    "content-length": "11",
                },
                stream=BytesStream(),
            )

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(audio, "resolve_audio", resolve)
    monkeypatch.setattr(audio.httpx, "AsyncClient", Client)
    return requests, closed


def test_range_stream_preserves_headers_and_releases_connection(client, monkeypatch):
    requests, closed = mock_transport(monkeypatch)
    response = client.get("/audio/abcdefghijk", headers={"Range": "bytes=0-10"})
    assert response.status_code == 206
    assert response.content == b"audio-bytes"
    assert response.headers["content-range"] == "bytes 0-10/100"
    assert response.headers["cache-control"] == "private, no-store"
    assert requests[0].headers["range"] == "bytes=0-10"
    assert closed == [True]
    assert audio._active_streams == 0


@pytest.mark.parametrize("value", ["bad", "https:youtube", "a" * 12])
def test_invalid_video_ids_never_resolve(client, value):
    assert client.get(f"/audio/{value}").status_code == 422


@pytest.mark.parametrize("value", ["bytes=0-1,4-5", "items=0-4", "bytes=-", "bytes=hello"])
def test_invalid_ranges_are_rejected(client, value):
    assert client.get("/audio/abcdefghijk", headers={"Range": value}).status_code == 416


def test_disabled_audio(client, monkeypatch):
    monkeypatch.setenv("AUDIO_ENABLED", "false")
    assert client.get("/audio/status").json()["enabled"] is False
    assert client.get("/audio/abcdefghijk").status_code == 503


@pytest.mark.parametrize("url", [
    "https://localhost/file", "http://rr1.googlevideo.com/file",
    "https://googlevideo.com.evil.test/file", "https://googlevideo.com/file",
    "https://x.googlevideo.com:8443/file", "https://user:pass@x.googlevideo.com/file",
    "https://127.0.0.1/file",
])
def test_source_validation_rejects_arbitrary_destinations(url):
    with pytest.raises(HTTPException):
        audio.validate_source(url)


def test_upstream_redirects_are_not_followed_and_connection_is_closed(client, monkeypatch):
    requests, closed = mock_transport(monkeypatch, status=302)
    response = client.get("/audio/abcdefghijk")
    assert response.status_code == 502
    assert len(requests) == 1
    assert closed == [True]
    assert audio._active_streams == 0
    assert "secret" not in response.text


def test_stream_limit_rejects_excess_connections(client, monkeypatch):
    monkeypatch.setattr(audio, "_active_streams", audio.MAX_STREAMS)
    assert client.get("/audio/abcdefghijk").status_code == 503


@pytest.mark.asyncio
async def test_resolution_deduplicates_inflight_and_caches(monkeypatch):
    audio._cache.clear()
    audio._inflight.clear()
    calls = []
    original = audio._resolve_and_cache

    def extract(video_id):
        calls.append(video_id)
        return source()

    monkeypatch.setattr(audio, "_extract", extract)
    first, second = await asyncio.gather(
        audio.resolve_audio("abcdefghijk"), audio.resolve_audio("abcdefghijk")
    )
    assert first == second
    assert await audio.resolve_audio("abcdefghijk") == first
    assert calls == ["abcdefghijk"]
    audio._cache.clear()


def test_resolution_failure_releases_slot(client, monkeypatch):
    async def fail(video_id):
        raise HTTPException(502, "Unavailable")

    monkeypatch.setattr(audio, "resolve_audio", fail)
    assert client.get("/audio/abcdefghijk").status_code == 502
    assert audio._active_streams == 0


def sequence_transport(monkeypatch, statuses, *, content_type="audio/mp4", broken_stream=False):
    requests, closed, streams = [], [], []
    statuses = iter(statuses)

    class ObservableStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = 0
            streams.append(self)

        async def __aiter__(self):
            if broken_stream:
                raise httpx.ReadError("signed-url-secret")
            yield b"audio-bytes"

        async def aclose(self):
            self.closed += 1

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        def build_request(self, method, url, headers):
            request = httpx.Request(method, url, headers=headers)
            requests.append(request)
            return request

        async def send(self, request, stream):
            status = next(statuses)
            if isinstance(status, Exception):
                raise status
            return httpx.Response(status, headers={
                "content-type": content_type,
                "content-range": "bytes */100" if status == 416 else "bytes 0-10/100",
                "accept-ranges": "bytes", "content-length": "11",
            }, stream=ObservableStream())

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(audio.httpx, "AsyncClient", Client)
    return requests, closed, streams


@pytest.mark.parametrize("status", [403, 410])
def test_stale_source_is_resolved_again_once_and_range_preserved(client, monkeypatch, status):
    first = source()
    second = audio.AudioSource(first.url + "&fresh=true", {"User-Agent": "fresh"}, time.monotonic() + 60)
    resolver = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(audio, "resolve_audio", resolver)
    audio._cache["abcdefghijk"] = first
    requests, closed, streams = sequence_transport(monkeypatch, [status, 206])
    response = client.get("/audio/abcdefghijk", headers={"Range": "bytes=0-10"})
    assert response.status_code == 206
    assert response.content == b"audio-bytes"
    assert resolver.await_count == 2
    assert "abcdefghijk" not in audio._cache
    assert len(requests) == 2
    assert requests[1].url == second.url
    assert requests[1].headers["range"] == "bytes=0-10"
    assert requests[1].headers["user-agent"] == "fresh"
    assert [stream.closed for stream in streams] == [1, 1]
    assert closed == [True]
    assert audio._active_streams == 0


def test_repeated_stale_source_failure_stops_retrying(client, monkeypatch):
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    requests, closed, streams = sequence_transport(monkeypatch, [403, 403])
    response = client.get("/audio/abcdefghijk")
    assert response.status_code == 502
    assert len(requests) == 2
    assert [stream.closed for stream in streams] == [1, 1]
    assert closed == [True]
    assert audio._active_streams == 0


def test_upstream_range_error_preserves_length_and_closes(client, monkeypatch):
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    _, closed, streams = sequence_transport(monkeypatch, [416])
    response = client.get("/audio/abcdefghijk", headers={"Range": "bytes=1000-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */100"
    assert streams[0].closed == 1
    assert closed == [True]
    assert audio._active_streams == 0


def test_provider_html_is_not_streamed_as_audio(client, monkeypatch):
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    _, closed, streams = sequence_transport(monkeypatch, [200], content_type="text/html")
    assert client.get("/audio/abcdefghijk").status_code == 502
    assert streams[0].closed == 1
    assert closed == [True]
    assert audio._active_streams == 0


def test_connection_error_does_not_expose_upstream_url_or_leak_slot(client, monkeypatch):
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    _, closed, _ = sequence_transport(monkeypatch, [httpx.ConnectError("signed-url-secret")])
    response = client.get("/audio/abcdefghijk")
    assert response.status_code == 502
    assert "signed-url-secret" not in response.text
    assert closed == [True]
    assert audio._active_streams == 0


@pytest.mark.asyncio
async def test_disconnection_before_body_starts_releases_connection(monkeypatch):
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    _, closed, streams = sequence_transport(monkeypatch, [200])
    scope = {"type": "http", "method": "GET", "path": "/audio/abcdefghijk", "headers": [], "asgi": {"spec_version": "2.4"}}
    response = await audio.stream_audio("abcdefghijk", Request(scope))
    assert audio._active_streams == 1

    async def send(message):
        assert message["type"] == "http.response.start"
        raise OSError("client disconnected before receiving headers")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)
    assert streams[0].closed == 1
    assert closed == [True]
    assert audio._active_streams == 0


@pytest.mark.asyncio
async def test_disconnect_midstream_closes_upstream(monkeypatch):
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    _, closed, streams = sequence_transport(monkeypatch, [200])
    scope = {"type": "http", "method": "GET", "path": "/audio/abcdefghijk", "headers": [], "asgi": {"spec_version": "2.4"}}
    response = await audio.stream_audio("abcdefghijk", Request(scope))

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("disconnected midstream")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)
    assert streams[0].closed == 1
    assert closed == [True]
    assert audio._active_streams == 0


def test_stream_failures_hide_signed_urls_and_close_resources(client, monkeypatch):
    monkeypatch.setattr(audio, "resolve_audio", AsyncMock(return_value=source()))
    _, closed, streams = sequence_transport(monkeypatch, [200], broken_stream=True)
    with pytest.raises(RuntimeError, match="The audio connection was interrupted") as caught:
        client.get("/audio/abcdefghijk")
    assert "signed-url-secret" not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert streams[0].closed == 1
    assert closed == [True]
    assert audio._active_streams == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_resolver(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def resolve(identity):
        calls.append(identity)
        started.set()
        await release.wait()
        return source()

    monkeypatch.setattr(audio, "_resolve_and_cache", resolve)
    first = asyncio.create_task(audio.resolve_audio("abcdefghijk"))
    await started.wait()
    second = asyncio.create_task(audio.resolve_audio("abcdefghijk"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not audio._inflight["abcdefghijk"].cancelled()
    release.set()
    assert (await second).url == source().url
    assert calls == ["abcdefghijk"]
    await asyncio.sleep(0)
    assert not audio._inflight


@pytest.mark.asyncio
async def test_resolver_capacity_is_bounded_without_breaking_shared_requests(monkeypatch):
    release = asyncio.Event()

    async def resolve(identity):
        await release.wait()
        return source()

    monkeypatch.setattr(audio, "_resolve_and_cache", resolve)
    monkeypatch.setattr(audio, "MAX_RESOLVERS", 2)
    tasks = [asyncio.create_task(audio.resolve_audio(identity)) for identity in ("abcdefghijk", "12345678901")]
    await asyncio.sleep(0)
    try:
        with pytest.raises(HTTPException) as caught:
            await audio.resolve_audio("zzzzzzzzzzz")
        assert caught.value.status_code == 503
        shared = asyncio.create_task(audio.resolve_audio("abcdefghijk"))
        release.set()
        await asyncio.gather(*tasks, shared)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not audio._inflight


@pytest.mark.asyncio
async def test_resolver_timeout_keeps_actual_work_bounded_and_reusable(monkeypatch):
    release = asyncio.Event()
    calls = []

    async def resolve(identity):
        calls.append(identity)
        await release.wait()
        return source()

    monkeypatch.setattr(audio, "_resolve_and_cache", resolve)
    monkeypatch.setattr(audio, "RESOLVE_TIMEOUT", 0.01)
    with pytest.raises(HTTPException) as caught:
        await audio.resolve_audio("abcdefghijk")
    assert caught.value.status_code == 504
    task = audio._inflight["abcdefghijk"]
    assert not task.done()
    release.set()
    await task
    await asyncio.sleep(0)
    assert calls == ["abcdefghijk"]
    assert not audio._inflight


@pytest.mark.parametrize("url", ["https://x.googlevideo.com:invalid/file", "https://[invalid/file"])
def test_malformed_source_urls_produce_controlled_error(url):
    with pytest.raises(HTTPException) as caught:
        audio.validate_source(url)
    assert caught.value.status_code == 502
