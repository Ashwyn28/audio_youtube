"""Translate YouTube responses into the stable AudioTube API format."""

from html import unescape
import re
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException


def duration_seconds(value):
    if not isinstance(value, str):
        return 0
    match = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_query_response(data):
    videos = []
    for item in data.get("items", []):
        identity = item.get("id", {})
        video_id = identity.get("videoId") if isinstance(identity, dict) else identity
        snippet = item.get("snippet", {})
        if not video_id or not snippet:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = thumbnails.get("medium") or thumbnails.get("default") or {}
        videos.append({
            "id": video_id,
            "title": unescape(snippet.get("title", "Untitled video")),
            "timestamp": snippet.get("publishedAt") or snippet.get("publishTime", ""),
            "channelId": snippet.get("channelId", ""),
            "channelTitle": unescape(snippet.get("channelTitle", "")),
            "thumbnail": thumbnail.get("url", ""),
            "duration": duration_seconds(item.get("contentDetails", {}).get("duration")),
        })
    return videos


def parse_video_page(data):
    return {"items": parse_query_response(data), "nextPageToken": data.get("nextPageToken")}


def parse_channel_page(data):
    channels = []
    for item in data.get("items", []):
        identity = item.get("id", {})
        channel_id = identity.get("channelId") if isinstance(identity, dict) else identity
        if channel_id:
            channels.append({
                "id": channel_id,
                "name": unescape(item.get("snippet", {}).get("title", "Untitled channel")),
            })
    return {"items": channels, "nextPageToken": data.get("nextPageToken")}


def parse_channel_response(data):
    channels = parse_channel_page(data)["items"]
    if not channels:
        raise HTTPException(404, "No matching channel found. Try another name.")
    return channels[0]


def parse_channels_response(channels_data):
    return sorted(channels_data, key=lambda video: video["timestamp"], reverse=True)


def youtube_reference(value):
    """Parse supported links without requesting user-supplied hosts."""
    candidate = value.strip()
    if not candidate.startswith(("https://", "http://")):
        if candidate.startswith(("youtube.com/", "www.youtube.com/", "youtu.be/", "m.youtube.com/")):
            candidate = "https://" + candidate
        else:
            return None
    try:
        parsed = urlparse(candidate)
        if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return None
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    parts = parsed.path.strip("/").split("/")
    if host == "youtu.be":
        kind, identity = "video", parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path.rstrip("/") == "/watch":
            kind, identity = "video", parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            kind, identity = "video", parts[1]
        elif len(parts) >= 2 and parts[0] in {"channel", "user"}:
            kind, identity = parts[0], parts[1]
        elif parts[0].startswith("@"):
            kind, identity = "handle", parts[0]
        else:
            return None
    else:
        return None
    if kind == "video" and not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity):
        return None
    if kind != "video" and not re.fullmatch(r"[@\w.-]{1,100}", identity):
        return None
    return kind, identity
