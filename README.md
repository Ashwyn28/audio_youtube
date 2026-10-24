# AudioTube

A minimal listening app with saved YouTube channels, recent uploads, search,
a native background-capable audio player, queue, saved items and listening history.
The [frontend repository](https://github.com/Ashwyn28/audio_youtube_frontend) keeps
the original monochrome design and bottom search bar. The API and frontend remain
separate repositories.

## Run locally

Use Python 3.11+ and Node 22+. A YouTube Data API v3 key is required for discovery.
The audio resolver uses yt-dlp and the Node runtime; no audio files are saved.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Set API_KEY in .env; keep ENVIRONMENT=development for local configuration.
uvicorn main:app --reload --port 8000
```

In another terminal:

```sh
cd ../audio_youtube_frontend/audio-youtube-frontend
npm ci
cp .env.example .env
npm run dev
```

Open http://localhost:3000. API documentation is at http://localhost:8000/docs.

## Everyday use

- Add channels with `+`; choose the right search result, reorder or remove channels.
- Choose a channel pill to browse its uploads; use All channels for the combined feed.
- Search for videos or paste a supported YouTube video/channel link.
- Play immediately, play next, queue, save for later, or mark an item finished using its menu.
- Use each card's Play/Pause button and seek bar, just like the original interface.
- Settings holds playback speed and the sleep timer; device media controls support skipping.
- Light and dark mode follow your system preference automatically.
- During playback, the logo becomes a live seven-band frequency display driven
  by the audio itself, then returns to the original mark when paused or buffering.
  It respects reduced motion and keeps the original logo if Web Audio is unavailable.
- Open Queue to reorder upcoming items and control automatic continuation.
- Saved and History restore your library and listening progress on this device.
- Settings exports/imports a library JSON file or clears local personal data.

Restored and imported sessions are paused. There are no accounts or cross-device
sync. Channels are capped at 30; the queue and saved list at 100 each; history at
200 items. Browser storage failures are shown explicitly. Export a library before
clearing browser storage or switching devices.

## Background playback

The player uses a native HTML audio element and Media Session actions where the
browser supports them. The API resolves an audio stream for a strict video ID and
proxies byte ranges so seeking and browser media controls can work without
exposing signed provider URLs. It does not buffer the entire media file or write
media to disk. Expired stream URLs are refreshed once automatically.

Playback still depends on the browser, operating system, network and upstream
provider. Some items are unavailable or require authentication and will fail with
a retry/open-on-YouTube option. A first user interaction may be needed to start
playback. Actual locked-phone behavior must be verified on the intended device;
mobile viewport simulation is not proof of iOS or Android background playback.
The sleep timer uses browser events/timers, so precise timing while the OS
suspends a page is not guaranteed.

Long-lived audio streaming requires the HTTP-server deployment below. The legacy
Lambda target is metadata-only and explicitly disables audio. Do not use an
API Gateway/Lambda metadata endpoint as the frontend's audio backend.

## Configuration

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `development` reads `API_KEY`; other values use AWS Parameter Store. |
| `API_KEY` | YouTube Data API v3 key for direct local/container configuration. |
| `CORS_ORIGINS` | Comma-separated frontend origins; include the actual scheme, host and port. |
| `AUDIO_ENABLED` | `true` by default; `false` disables audio resolution/streaming. |
| `PUBLIC_API_BASE_URL` | Browser-reachable API URL, defaults to `http://localhost:8000`. |
| `FRONTEND_ORIGIN` | Compose frontend origin, defaults to `http://localhost:3000`. |
| `ORIGIN` | Frontend Node server's public origin when started outside Compose. |

For phone testing, use the computer's reachable hostname/IP for the frontend and
`PUBLIC_API_BASE_URL`, bind both servers to the LAN, and add the frontend origin
to `CORS_ORIGINS`. Use HTTPS for a real hosted deployment and supported media
features. Never put `API_KEY` in a public frontend environment variable.

## API

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | Process health and whether a discovery key is configured; no upstream probe. |
| GET | `/videos/search?query=...` | Video results; add `paginated=true` for a page. |
| GET | `/channels/search?query=...` | First matching channel; `paginated=true` returns choices. |
| GET | `/channels/{id}/latest` | Uploads; `paginated=true` enables continuation. |
| POST | `/channels/latest` | JSON array of channel IDs -> combined, deduplicated recent feed. |
| GET | `/resolve?url=...` | Typed video/channel result for supported YouTube links. |
| GET | `/audio/status` | Whether audio is enabled and yt-dlp installed; not a provider probe. |
| GET | `/audio/{video_id}` | Native audio stream; supports a single HTTP byte range. |

Paginated results use `{ "items": [...], "nextPageToken": "..." }`; send the token
back as `page_token`. Video metadata includes `id`, `title`, `timestamp`,
`channelId`, `channelTitle`, `thumbnail`, and numeric duration in seconds.
Requests have timeouts, bounded concurrency, bounded metadata caching and route
rate limits. Upload feeds use channel uploads playlists instead of repeated
searches. Audio resolution and active streams have separate capacity bounds.
Caches and limits are per process, not a distributed shared quota.

## Production builds

```sh
cd ../audio_youtube_frontend/audio-youtube-frontend
npm ci
npm run check
npm test
npm run build
PUBLIC_API_BASE_URL=https://your-api.example ORIGIN=https://your-app.example npm start
```

The frontend uses SvelteKit's Node adapter; `npm start` serves the production build.
The backend production image runs Uvicorn with Python and Node for resolution:

```sh
docker build --target production -t audiotube-api .
docker build --target production -t audiotube-web ../audio_youtube_frontend/audio-youtube-frontend
```

For the complete local container app, clone the frontend repository alongside this
repository, configure root `.env`, then:

```sh
docker compose up --build
```

Compose uses `../audio_youtube_frontend/audio-youtube-frontend` by default. Set
`FRONTEND_CONTEXT` to override that source directory (for example `./frontend` for
an isolated development copy). It exposes the UI on 3000 and API on 8000. For a hosted release, configure
HTTPS, browser-reachable API/CORS/origin values, streaming-compatible proxy timeouts,
and capacity/bandwidth monitoring. Keep yt-dlp updated when provider behavior changes.

The optional legacy metadata-only AWS image is built with `--target lambda` and
uses `main.handler`. It requires the `youtube_api_key` SSM parameter plus runtime
`ssm:GetParameters` permissions and, if applicable, KMS decrypt permission. Production HTTP hosting is described in [deploy/README.md](deploy/README.md).

## Validation

```sh
python -m pytest -q
cd ../audio_youtube_frontend/audio-youtube-frontend
npm run check
npm test
npm run build
npm run test:e2e
```

Browser tests use installed Chrome locally. Alternatively install Playwright's
Chromium and set `PLAYWRIGHT_CHANNEL=chromium`. Tests start the production app
when needed, use mocked discovery and local audio fixtures, and cover desktop
and mobile viewports. They do not consume YouTube API quota or prove physical
phone background playback. CI installs Chromium and runs the same checks.

See [RELEASE.md](RELEASE.md) for the release evidence and remaining device checks.

## Layout

- `main.py`, `services.py`, `utils.py`: discovery API and provider integration.
- `audio.py`: native audio resolution and streaming.
- `test_main.py`, `test_audio.py`: mocked backend coverage.
- [Frontend](https://github.com/Ashwyn28/audio_youtube_frontend): SvelteKit app, unit/browser tests, Vercel and Docker builds.
- `.github/workflows/ci.yml`: backend validation; frontend/browser checks run in the frontend repository.
