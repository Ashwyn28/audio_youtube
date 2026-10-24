from contextlib import asynccontextmanager
import os
import re

import boto3
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from services import YoutubeAPI
from utils import (parse_channel_page, parse_channel_response, parse_channels_response,
                   parse_query_response, parse_video_page, youtube_reference)

load_dotenv()


def get_ssm_parameters(names):
    try:
        response = boto3.Session().client("ssm").get_parameters(Names=names, WithDecryption=True)
        parameters = {param["Name"]: param["Value"] for param in response["Parameters"]}
        if any(not parameters.get(name) for name in names):
            raise RuntimeError("Missing required parameter")
        return parameters
    except Exception:
        raise RuntimeError("Could not load YouTube configuration from Parameter Store.") from None


@asynccontextmanager
async def lifespan(app):
    if os.getenv("ENVIRONMENT") == "development":
        app.state.ssm_params = {"youtube_api_key": os.getenv("API_KEY")}
    else:
        app.state.ssm_params = get_ssm_parameters(["youtube_api_key"])
    app.state.api = YoutubeAPI(app)
    try:
        yield
    finally:
        await app.state.api.close()


app = FastAPI(title="AudioTube API", lifespan=lifespan)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS",
    "http://localhost,http://localhost:3000,http://127.0.0.1:3000,https://orbbits.io,https://audio-youtube.orbbits.io",
).split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins,
                   allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Range"],
                   expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"])


@app.get("/")
async def read_root():
    return {"message": "Hello world"}


@app.get("/health")
async def health():
    return {"status": "ok", "youtubeConfigured": bool(app.state.api.API_KEY)}


@app.get("/videos/search")
@limiter.limit("30/minute")
async def youtube_search(
    request: Request,
    query: str = Query(..., min_length=1, max_length=500, pattern=r".*\S.*"),
    page_token: str | None = Query(None, max_length=500),
    paginated: bool = False,
):
    res = await app.state.api.search_query(query.strip(), page_token)
    return parse_video_page(res) if paginated else parse_query_response(res)


@app.get("/channels/search")
@limiter.limit("20/minute")
async def search_channel(
    request: Request,
    query: str = Query(..., min_length=1, max_length=500, pattern=r".*\S.*"),
    page_token: str | None = Query(None, max_length=500),
    paginated: bool = False,
):
    res = await app.state.api.search_channel_id(query.strip(), page_token)
    return parse_channel_page(res) if paginated else parse_channel_response(res)


@app.get("/channels/{channel_id}/latest")
@limiter.limit("60/minute")
async def channel_latest(
    request: Request,
    channel_id: str = Path(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
    page_token: str | None = Query(None, max_length=500),
    paginated: bool = False,
):
    res = await app.state.api.search_channel_latest(channel_id, page_token=page_token)
    return parse_video_page(res) if paginated else parse_query_response(res)


@app.post("/channels/latest")
@limiter.limit("20/minute")
async def channels_latest(request: Request, query: list[str] = Body(..., max_length=30)):
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", channel) for channel in query):
        raise HTTPException(422, "Channel IDs must contain 1 to 100 letters, digits, underscores or hyphens.")
    return parse_channels_response(await app.state.api.search_channels_latest(query))


@app.get("/resolve")
@limiter.limit("20/minute")
async def resolve_link(request: Request, url: str = Query(..., min_length=1, max_length=500)):
    reference = youtube_reference(url)
    if not reference:
        raise HTTPException(422, "Paste a supported YouTube video or channel link.")
    if reference[0] == "video":
        return {"type": "video", "video": parse_query_response(await app.state.api.video_details(reference[1]))[0]}
    return {"type": "channel", "channel": await app.state.api.resolve_channel(reference)}


from audio import router as audio_router
app.include_router(audio_router)

handler = Mangum(app, lifespan="on")
