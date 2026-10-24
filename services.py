"""Bounded, cached access to YouTube metadata."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
import logging
from time import monotonic

import httpx
from fastapi import HTTPException

from utils import parse_channel_response, parse_query_response, youtube_reference

logger = logging.getLogger(__name__)


class YoutubeAPI:
    BASE_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self, app):
        self.API_KEY = app.state.ssm_params.get("youtube_api_key")
        self._requests = asyncio.Semaphore(5)
        self._cache = OrderedDict()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
        )

    async def close(self):
        await self._client.aclose()

    async def _get(self, resource, **params):
        if not self.API_KEY:
            raise HTTPException(503, "YouTube search is not configured.")
        cache_key = (resource, tuple(sorted(params.items())))
        cached = self._cache.get(cache_key)
        if cached and cached[0] > monotonic():
            self._cache.move_to_end(cache_key)
            return deepcopy(cached[1])
        try:
            async with self._requests:
                response = await self._client.get(
                    f"{self.BASE_URL}/{resource}", params={**params, "key": self.API_KEY}
                )
            if response.status_code != 200:
                status = response.status_code
                if status in (403, 429):
                    raise HTTPException(status, "YouTube is limiting requests. Please try again later.")
                if status == 404:
                    raise HTTPException(404, "This YouTube item is unavailable.")
                if status == 400:
                    raise HTTPException(400, "YouTube could not use this query or page token.")
                logger.warning("YouTube %s returned status %s", resource, status)
                raise HTTPException(502, "YouTube could not complete this request. Please try again.")
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("Unexpected YouTube response shape")
            if data.get("nextPageToken") is not None and not isinstance(data["nextPageToken"], str):
                raise ValueError("Unexpected YouTube pagination token")
            for item in data["items"]:
                if not isinstance(item, dict):
                    raise ValueError("Unexpected YouTube item")
                for field in ("snippet", "contentDetails", "status"):
                    if field in item and not isinstance(item[field], dict):
                        raise ValueError("Unexpected YouTube metadata")
                if "id" in item and not isinstance(item["id"], (str, dict)):
                    raise ValueError("Unexpected YouTube identifier")
        except httpx.TimeoutException:
            raise HTTPException(504, "YouTube took too long to respond. Please try again.") from None
        except httpx.RequestError:
            raise HTTPException(502, "YouTube is unavailable. Please try again.") from None
        except ValueError:
            raise HTTPException(502, "YouTube returned an unreadable response. Please try again.") from None
        self._cache[cache_key] = (monotonic() + 60, deepcopy(data))
        self._cache.move_to_end(cache_key)
        while len(self._cache) > 128:
            self._cache.popitem(last=False)
        return data

    async def _video_page(self, data):
        ids = [item.get("id", {}).get("videoId") for item in data["items"] if isinstance(item.get("id"), dict)]
        ids = list(dict.fromkeys(identity for identity in ids if identity))
        if not ids:
            return {"items": [], "nextPageToken": data.get("nextPageToken")}
        details = await self._get("videos", part="snippet,contentDetails,status", id=",".join(ids))
        by_id = {item["id"]: item for item in details["items"] if isinstance(item.get("id"), str)}
        return {
            "items": [by_id[identity] for identity in ids if identity in by_id and by_id[identity].get("status", {}).get("embeddable", True)],
            "nextPageToken": data.get("nextPageToken"),
        }

    async def search_query(self, q, page_token=None):
        reference = youtube_reference(q)
        if reference and reference[0] == "video":
            return await self.video_details(reference[1])
        if q.startswith(("https://", "http://")) and not reference:
            raise HTTPException(422, "Paste a supported YouTube video or channel link.")
        if reference:
            channel = await self.resolve_channel(reference)
            return await self.search_channel_latest(channel["id"], page_token=page_token)
        params = {"part": "snippet", "q": q, "type": "video", "maxResults": 12, "videoEmbeddable": "true"}
        if page_token:
            params["pageToken"] = page_token
        return await self._video_page(await self._get("search", **params))

    async def video_details(self, identity):
        data = await self._get("videos", part="snippet,contentDetails,status", id=identity)
        data["items"] = [item for item in data["items"] if item.get("status", {}).get("embeddable", True)]
        if not data["items"]:
            raise HTTPException(404, "This video is unavailable for playback.")
        return data

    async def search_channel_latest(self, channel_id, max_results=12, page_token=None):
        # Upload playlists avoid spending a search request for every followed channel.
        channel = await self._get("channels", part="contentDetails", id=channel_id)
        if not channel["items"]:
            raise HTTPException(404, "This channel is unavailable.")
        playlist_id = channel["items"][0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not playlist_id:
            return {"items": []}
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        uploads = await self._get("playlistItems", **params)
        search_shape = {
            "items": [{"id": {"videoId": item.get("contentDetails", {}).get("videoId")}} for item in uploads["items"]],
            "nextPageToken": uploads.get("nextPageToken"),
        }
        return await self._video_page(search_shape)

    async def search_channels_latest(self, channel_ids):
        responses = await asyncio.gather(*(
            self.search_channel_latest(channel_id, max_results=3)
            for channel_id in dict.fromkeys(channel_ids)
        ), return_exceptions=True)
        videos = {}
        failures = []
        for response in responses:
            if isinstance(response, Exception):
                failures.append(response)
                continue
            for video in parse_query_response(response):
                videos[video["id"]] = video
        # Do not present an incomplete feed as a fully successful refresh.
        if failures:
            raise failures[0]
        return list(videos.values())

    async def search_channel_id(self, q, page_token=None):
        reference = youtube_reference(q)
        if reference:
            if reference[0] == "video":
                videos = await self.video_details(reference[1])
                channel_id = videos["items"][0].get("snippet", {}).get("channelId")
                if not channel_id:
                    raise HTTPException(404, "The video's channel is unavailable.")
                return await self._get("channels", part="snippet", id=channel_id)
            return await self._channel_details(reference)
        if q.startswith(("https://", "http://")):
            raise HTTPException(422, "Paste a supported YouTube channel link.")
        if q.startswith("@"):
            return await self._channel_details(("handle", q))
        params = {"part": "snippet", "q": q, "type": "channel", "maxResults": 12}
        if page_token:
            params["pageToken"] = page_token
        return await self._get("search", **params)

    async def _channel_details(self, reference):
        kind, identity = reference
        key = {"channel": "id", "handle": "forHandle", "user": "forUsername"}[kind]
        return await self._get("channels", part="snippet", **{key: identity})

    async def resolve_channel(self, reference):
        return parse_channel_response(await self._channel_details(reference))
