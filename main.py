from fastapi import FastAPI, Query, Request
import boto3
from fastapi.middleware.cors import CORSMiddleware
from services import YoutubeAPI
from utils import parse_query_response, parse_channels_response

# from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from mangum import Mangum

# load_dotenv()

app = FastAPI()


@app.on_event("startup")
async def load_config():
    print("getting ssm paramers")
    app.state.ssm_params = get_ssm_parameters(["youtube_api_key"])
    app.state.api = YoutubeAPI(app)
    print(f"ssm parameters {app.state.ssm_params}")


def get_ssm_parameters(names):
    session = boto3.Session()
    ssm = session.client("ssm")
    parameters = {}
    try:
        response = ssm.get_parameters(Names=names, WithDecryption=True)
        for param in response["Parameters"]:
            parameters[param["Name"]] = param["Value"]
    except Exception as e:
        raise Exception(f"Failed to retrieve SSM parameters: {str(e)}")
    return parameters


# Initialize the Limiter
limiter = Limiter(key_func=get_remote_address)

# Attach the limiter to the app
app.state.limiter = limiter

# Add exception handler for rate limit exceeded
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
    return {"message": "Hello world"}


@app.get("/search")
async def youtube_search(query: str = Query(..., description="Search query")):
    res = await app.state.api.search_query(query)
    return parse_query_response(res)


@app.get("/channel/latest")
async def channel_latest(channel_id):
    res = await app.state.api.search_channel_latest(channel_id)
    return parse_query_response(res)


@app.post("/channels/latest")
@limiter.limit("4 per 5 minutes")
async def channels_latest(request: Request, query: list[str]):
    res = await app.staet.api.search_channels_latest(query)
    return parse_channels_response(res)
    # return [
    #     {
    #         'id': "8Ip8VOuI5Ho",
    #         'title': "test 1",
    #     },
    #     {
    #         'id': "8IaBF-5T-6U",
    #         'title': "test 2",
    #     },
    #     {
    #         'id': "WKhZQwWz6wU",
    #         'title': "test 3",
    #     },
    # ]


handler = Mangum(app, lifespan="on")
