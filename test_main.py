"""API contract and upstream-failure tests; no network or credentials required."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app, limiter
from utils import duration_seconds, youtube_reference


def video_item(identity, timestamp="2026-10-24T12:00:00Z", embeddable=True):
    return {
        "id": identity,
        "snippet": {
            "title": 'An &quot;episode&quot; &amp; more', "publishedAt": timestamp,
            "channelId": "channel-one", "channelTitle": "Channel &amp; Co",
            "thumbnails": {"medium": {"url": "https://example.com/image.jpg"}},
        },
        "contentDetails": {"duration": "PT1H2M3S"},
        "status": {"embeddable": embeddable},
    }


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("API_KEY", "test-secret-key")
    limiter.reset()
    with TestClient(app) as client:
        async def upstream(url, params):
            resource = url.rsplit("/", 1)[-1]
            if resource == "search":
                if params["type"] == "channel":
                    return httpx.Response(200, json={"items": [
                        {"id": {"channelId": "channel-one"}, "snippet": {"title": "Channel &amp; Co"}},
                        {"id": {"channelId": "channel-two"}, "snippet": {"title": "Other channel"}},
                    ], "nextPageToken": "channel-next"})
                return httpx.Response(200, json={"items": [{"id": {"videoId": "video-one"}}], "nextPageToken": "video-next"})
            if resource == "videos":
                return httpx.Response(200, json={"items": [video_item(identity) for identity in params["id"].split(",")]})
            if resource == "channels":
                identity = params.get("id", "channel-one")
                return httpx.Response(200, json={"items": [{
                    "id": identity, "snippet": {"title": "Channel &amp; Co"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "uploads-" + identity}},
                }]})
            if resource == "playlistItems":
                return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": "video-one"}}], "nextPageToken": "uploads-next"})
            raise AssertionError(f"Unexpected upstream resource {resource}")

        youtube_get = AsyncMock(side_effect=upstream)
        monkeypatch.setattr(app.state.api._client, "get", youtube_get)
        yield client, youtube_get


def test_root_and_health_do_not_request_youtube(api_client):
    client, upstream = api_client
    assert client.get("/").json() == {"message": "Hello world"}
    assert client.get("/health").json() == {"status": "ok", "youtubeConfigured": True}
    upstream.assert_not_called()


def test_search_includes_decoded_metadata_and_duration(api_client):
    client, upstream = api_client
    result = client.get("/videos/search", params={"query": " episode "})
    assert result.status_code == 200
    assert result.json()[0] == {
        "id": "video-one", "title": 'An "episode" & more',
        "timestamp": "2026-10-24T12:00:00Z", "channelId": "channel-one",
        "channelTitle": "Channel & Co", "thumbnail": "https://example.com/image.jpg", "duration": 3723,
    }
    assert upstream.call_args_list[0].kwargs["params"]["q"] == "episode"
    assert upstream.call_args_list[0].kwargs["params"]["videoEmbeddable"] == "true"
    assert upstream.call_args_list[1].kwargs["params"]["part"] == "snippet,contentDetails,status"


def test_video_pagination_preserves_token(api_client):
    client, upstream = api_client
    result = client.get("/videos/search", params={"query": "episode", "paginated": True, "page_token": "requested"})
    assert result.json()["nextPageToken"] == "video-next"
    assert upstream.call_args_list[0].kwargs["params"]["pageToken"] == "requested"


def test_channel_search_keeps_first_result_contract(api_client):
    client, _ = api_client
    assert client.get("/channels/search", params={"query": "channel"}).json() == {"id": "channel-one", "name": "Channel & Co"}


def test_channel_search_supports_multiple_results_and_pagination(api_client):
    client, upstream = api_client
    result = client.get("/channels/search", params={"query": "channel", "paginated": True, "page_token": "requested"})
    assert result.json() == {"items": [{"id": "channel-one", "name": "Channel & Co"}, {"id": "channel-two", "name": "Other channel"}], "nextPageToken": "channel-next"}
    assert upstream.call_args.kwargs["params"]["pageToken"] == "requested"


def test_channel_feed_uses_uploads_playlist(api_client):
    client, upstream = api_client
    result = client.get("/channels/channel-one/latest", params={"paginated": True, "page_token": "requested"})
    assert result.status_code == 200
    assert result.json()["nextPageToken"] == "uploads-next"
    resources = [call.args[0].rsplit("/", 1)[-1] for call in upstream.call_args_list]
    assert resources == ["channels", "playlistItems", "videos"]
    assert upstream.call_args_list[1].kwargs["params"]["pageToken"] == "requested"


def test_combined_feed_deduplicates_channels_and_videos(api_client):
    client, upstream = api_client
    result = client.post("/channels/latest", json=["channel-one", "channel-one", "channel-two"])
    assert result.status_code == 200
    assert [video["id"] for video in result.json()] == ["video-one"]
    channel_calls = [call for call in upstream.call_args_list if call.args[0].endswith("/channels")]
    assert len(channel_calls) == 2


def test_empty_saved_channels_needs_no_upstream_request(api_client):
    client, upstream = api_client
    assert client.post("/channels/latest", json=[]).json() == []
    upstream.assert_not_called()


@pytest.mark.parametrize("path", ["/videos/search", "/channels/search"])
@pytest.mark.parametrize("query", [None, "", "   ", "x" * 501])
def test_search_validates_input(api_client, path, query):
    client, upstream = api_client
    result = client.get(path, params={} if query is None else {"query": query})
    assert result.status_code == 422
    upstream.assert_not_called()


@pytest.mark.parametrize("body", [[""], ["bad/id"], ["x" * 101], ["channel"] * 31, "channel"])
def test_feed_validates_input(api_client, body):
    client, upstream = api_client
    assert client.post("/channels/latest", json=body).status_code == 422
    upstream.assert_not_called()


def test_channel_search_empty_results_have_actionable_404(api_client):
    client, upstream = api_client
    upstream.side_effect = None
    upstream.return_value = httpx.Response(200, json={"items": []})
    assert client.get("/channels/search", params={"query": "missing"}).status_code == 404
    page = client.get("/channels/search", params={"query": "missing", "paginated": True})
    assert page.json() == {"items": [], "nextPageToken": None}


@pytest.mark.parametrize("status,expected", [(403, 403), (429, 429), (400, 400), (404, 404), (500, 502), (401, 502)])
def test_upstream_errors_are_safe(api_client, status, expected):
    client, upstream = api_client
    upstream.side_effect = None
    upstream.return_value = httpx.Response(status, text="secret credential test-secret-key")
    result = client.get("/videos/search", params={"query": "episode"})
    assert result.status_code == expected
    assert "test-secret-key" not in result.text
    assert "credential" not in result.text


@pytest.mark.parametrize("failure,expected", [(httpx.ReadTimeout("secret"), 504), (httpx.ConnectError("secret"), 502)])
def test_transport_errors(api_client, failure, expected):
    client, upstream = api_client
    upstream.side_effect = failure
    response = client.get("/videos/search", params={"query": "episode"})
    assert response.status_code == expected
    assert "secret" not in response.text


@pytest.mark.parametrize("payload", [{}, [], {"items": None}, {"items": [None]},
                                      {"items": [{"id": 123}]},
                                      {"items": [{"snippet": []}]},
                                      {"items": [], "nextPageToken": {}}])
def test_malformed_upstream_shape_is_502(api_client, payload):
    client, upstream = api_client
    upstream.side_effect = None
    upstream.return_value = httpx.Response(200, json=payload)
    assert client.get("/videos/search", params={"query": "episode"}).status_code == 502


def test_invalid_json_is_502(api_client):
    client, upstream = api_client
    upstream.side_effect = None
    upstream.return_value = httpx.Response(200, text="not json")
    assert client.get("/videos/search", params={"query": "episode"}).status_code == 502


def test_repeated_search_uses_cache(api_client):
    client, upstream = api_client
    first = client.get("/videos/search", params={"query": "episode"})
    second = client.get("/videos/search", params={"query": "episode"})
    assert first.json() == second.json()
    assert upstream.call_count == 2  # one search + one details batch, both cached


def test_unembeddable_and_deleted_videos_are_filtered(api_client):
    client, upstream = api_client
    upstream.side_effect = [
        httpx.Response(200, json={"items": [{"id": {"videoId": "blocked"}}, {"id": {"videoId": "deleted"}}]}),
        httpx.Response(200, json={"items": [video_item("blocked", embeddable=False)]}),
    ]
    assert client.get("/videos/search", params={"query": "episode"}).json() == []


def test_resolve_video_link(api_client):
    client, upstream = api_client
    result = client.get("/resolve", params={"url": "https://youtu.be/abcdefghijk?t=50"})
    assert result.status_code == 200
    assert result.json()["type"] == "video"
    assert result.json()["video"]["id"] == "abcdefghijk"
    assert upstream.call_args.args[0].endswith("/videos")


def test_pasted_video_link_search_skips_search_quota(api_client):
    client, upstream = api_client
    result = client.get("/videos/search", params={"query": "https://www.youtube.com/watch?v=abcdefghijk", "paginated": True})
    assert result.json()["items"][0]["id"] == "abcdefghijk"
    assert upstream.call_count == 1
    assert upstream.call_args.args[0].endswith("/videos")


def test_resolve_channel_handle(api_client):
    client, upstream = api_client
    result = client.get("/resolve", params={"url": "https://youtube.com/@example/videos"})
    assert result.json() == {"type": "channel", "channel": {"id": "channel-one", "name": "Channel & Co"}}
    assert upstream.call_args.kwargs["params"]["forHandle"] == "@example"


@pytest.mark.parametrize("url", ["http://127.0.0.1/secret", "https://youtube.com.evil.com/watch?v=abcdefghijk", "https://evil@youtube.com/watch?v=abcdefghijk", "https://youtube.com:invalid/watch?v=abcdefghijk", "https://youtube.com/watch?v=bad", "https://youtube.com/playlist?list=abc"])
def test_resolve_rejects_unsupported_or_unsafe_links(api_client, url):
    client, upstream = api_client
    assert client.get("/resolve", params={"url": url}).status_code == 422
    upstream.assert_not_called()


def test_missing_key_is_actionable_without_upstream_request(api_client):
    client, upstream = api_client
    app.state.api.API_KEY = None
    assert client.get("/videos/search", params={"query": "episode"}).status_code == 503
    upstream.assert_not_called()


def test_rate_limit_is_applied(api_client):
    client, _ = api_client
    for _ in range(30):
        assert client.get("/videos/search", params={"query": "episode"}).status_code == 200
    assert client.get("/videos/search", params={"query": "episode"}).status_code == 429


@pytest.mark.parametrize("value,expected", [("PT5M", 300), ("PT2H", 7200), ("P1DT1H1M1S", 90061), ("P0D", 0), (None, 0)])
def test_duration_parser(value, expected):
    assert duration_seconds(value) == expected


def test_url_parser_accepts_shorts_and_mobile():
    assert youtube_reference("https://m.youtube.com/shorts/abcdefghijk") == ("video", "abcdefghijk")
