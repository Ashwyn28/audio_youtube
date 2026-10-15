# Audio YouTube API

A small FastAPI backend for searching YouTube videos and channels and retrieving
their latest uploads. It returns video metadata; it does not download or extract audio.

## Local setup

Use Python 3.11, matching the Docker image, and a YouTube Data API v3 key.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `API_KEY` in `.env`, then start the server:

```sh
uvicorn main:app --reload
```

The API runs at http://localhost:8000. Interactive API documentation is available
at http://localhost:8000/docs.

### Configuration

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | Set to `development` to load the YouTube key from `API_KEY`. Any other value, including unset, uses AWS Systems Manager Parameter Store. |
| `API_KEY` | YouTube Data API v3 key for local development. |

The application loads `.env` on startup. Keep real credentials out of version control.

## API

| Method | Path | Input | Result |
| --- | --- | --- | --- |
| GET | `/` | None | Greeting; does not check YouTube connectivity. |
| GET | `/videos/search` | Required `query` parameter | Up to one matching video. |
| GET | `/channels/search` | Required `query` parameter | First matching channel as `id` and `name`. |
| GET | `/channels/{channel_id}/latest` | Channel ID in the path | Up to three latest results. |
| POST | `/channels/latest` | JSON array of channel IDs | Latest result per channel, sorted newest first. |

Video results contain `id`, `title`, and `timestamp`:

```json
[
  {
    "id": "video-id",
    "title": "Example video",
    "timestamp": "2024-10-01T12:00:00Z"
  }
]
```

```sh
curl 'http://localhost:8000/videos/search?query=python'
curl 'http://localhost:8000/channels/search?query=YouTube'
curl 'http://localhost:8000/channels/UCBJycsmduvYEL83R_U4JriQ/latest'
curl -X POST 'http://localhost:8000/channels/latest' \
  -H 'Content-Type: application/json' \
  -d '["UCBJycsmduvYEL83R_U4JriQ", "UCEcrRXW3oEYfUctetZTAWLw"]'
```

Searches use the YouTube Data API and consume its quota. Upstream HTTP errors are
returned with the upstream status code. Empty channel searches and unexpected
YouTube response shapes are not currently handled explicitly.

## Docker development

Create `.env` as described above, then run:

```sh
docker compose up --build
```

Compose starts the development image on port 8000 and mounts the repository at
`/var/task` so Uvicorn reloads source changes. It loads the API key from `.env`
and sets `ENVIRONMENT=development`.

## Tests

```sh
python -m pytest -q
```

Tests run application startup in development mode with a dummy key and mock the
YouTube HTTP requests. No live YouTube requests or AWS credentials are needed.

## AWS Lambda

The production image uses the AWS Lambda Python 3.11 base image and the Mangum
handler at `main.handler`:

```sh
docker build --target production -t audio-youtube:production .
```

For Lambda, leave `ENVIRONMENT` unset or set it to a value other than
`development`. Create a Systems Manager parameter named `youtube_api_key` in the
runtime's AWS region. The execution role needs `ssm:GetParameters` for that
parameter and, when using a customer-managed KMS key, permission to decrypt it.
The runtime also needs network access to YouTube and Systems Manager.

Image publishing, Lambda provisioning, and an HTTP integration such as API Gateway
are not configured in this repository.

## Project layout

- `main.py`: routes, startup configuration, CORS, and Lambda handler.
- `services.py`: YouTube Data API requests.
- `utils.py`: response parsing and chronological sorting.
- `test_main.py`: mocked API tests.

## Current limitations

- Requests to YouTube are synchronous inside async handlers and have no explicit timeout.
- A rate limiter is initialized, but no endpoint currently has a rate limit applied.
- CORS allows the origins listed in `main.py`; update that list for other frontends.
