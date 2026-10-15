from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("API_KEY", "test-key")
    youtube_get = Mock()
    monkeypatch.setattr("services.requests.get", youtube_get)
    with TestClient(app) as client:
        yield client, youtube_get


def youtube_response(items):
    return Mock(status_code=200, json=Mock(return_value={"items": items}))


def video_item(video_id, timestamp):
    return {
        "id": {"videoId": video_id},
        "snippet": {"title": f"Video {video_id}", "publishTime": timestamp},
    }


def test_root(api_client):
    client, youtube_get = api_client

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello world"}
    youtube_get.assert_not_called()


def test_video_search(api_client):
    client, youtube_get = api_client
    youtube_get.return_value = youtube_response(
        [video_item("one", "2024-10-01T12:00:00Z")]
    )

    response = client.get("/videos/search", params={"query": "test query"})

    assert response.status_code == 200
    assert response.json() == [
        {"id": "one", "title": "Video one", "timestamp": "2024-10-01T12:00:00Z"}
    ]
    assert youtube_get.call_args.kwargs["params"] == {
        "part": "snippet",
        "q": "test query",
        "type": "video",
        "maxResults": 1,
        "key": "test-key",
    }


def test_channel_search(api_client):
    client, youtube_get = api_client
    youtube_get.return_value = youtube_response(
        [{"id": {"channelId": "channel-one"}, "snippet": {"title": "Example"}}]
    )

    response = client.get("/channels/search", params={"query": "Example"})

    assert response.status_code == 200
    assert response.json() == {"id": "channel-one", "name": "Example"}
    assert youtube_get.call_args.kwargs["params"]["type"] == "channel"


def test_channel_latest(api_client):
    client, youtube_get = api_client
    youtube_get.return_value = youtube_response(
        [video_item("latest", "2024-10-02T12:00:00Z")]
    )

    response = client.get("/channels/channel-one/latest")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "latest", "title": "Video latest", "timestamp": "2024-10-02T12:00:00Z"}
    ]
    params = youtube_get.call_args.kwargs["params"]
    assert params["channelId"] == "channel-one"
    assert params["order"] == "date"
    assert params["maxResults"] == 3


def test_channels_latest_sorted_newest_first(api_client):
    client, youtube_get = api_client
    youtube_get.side_effect = [
        youtube_response([video_item("older", "2024-10-01T12:00:00Z")]),
        youtube_response([video_item("newer", "2024-10-02T12:00:00Z")]),
    ]

    response = client.post("/channels/latest", json=["channel-one", "channel-two"])

    assert response.status_code == 200
    assert response.json() == [
        {"id": "newer", "title": "Video newer", "timestamp": "2024-10-02T12:00:00Z"},
        {"id": "older", "title": "Video older", "timestamp": "2024-10-01T12:00:00Z"},
    ]
    calls = youtube_get.call_args_list
    assert [call.kwargs["params"]["channelId"] for call in calls] == [
        "channel-one", "channel-two"
    ]
    assert all(call.kwargs["params"]["maxResults"] == 1 for call in calls)


@pytest.mark.parametrize("path", ["/videos/search", "/channels/search"])
def test_search_requires_query(api_client, path):
    client, youtube_get = api_client

    assert client.get(path).status_code == 422
    youtube_get.assert_not_called()


def test_youtube_error_status_is_preserved(api_client):
    client, youtube_get = api_client
    youtube_get.return_value = Mock(status_code=403, text="Quota exceeded")

    response = client.get("/videos/search", params={"query": "test"})

    assert response.status_code == 403
    assert "Quota exceeded" in response.json()["detail"]
