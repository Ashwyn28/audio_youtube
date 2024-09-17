from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from services import YoutubeAPI
from utils import parse_query_response, parse_channels_response
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
api = YoutubeAPI()

origins = [
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allows specific origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)


@app.get("/")
async def read_root():
    return {"message": "Hello, World!"}


@app.get("/search")
async def youtube_search(query: str = Query(..., description="Search query")):
    res = await api.search_query(query)
    return parse_query_response(res)


@app.get("/channel/latest")
async def channel_latest(channel_id):
    res = await api.search_channel_latest(channel_id)
    return parse_query_response(res)


@app.post("/channels/latest")
async def channels_latest(query: list[str]):
    res = await api.search_channels_latest(query)
    return parse_channels_response(res)
