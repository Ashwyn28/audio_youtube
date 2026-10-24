# AudioTube release evidence

Implementation and verification performed on 24 October 2026. This is a local
release candidate; it has not been published or deployed.

## Implemented scope

- Original monochrome presentation retained, with responsive desktop/mobile grids,
  outlined channel pills, item actions and bottom search.
- Channel search/selection, adding/removal/reordering, combined feed, channel views,
  paginated video results, refresh/retry and pasted-link resolution.
- Original per-card Play/Pause, time and seek controls backed by native audio;
  Media Session actions, Settings speed/sleep options, configurable queue
  continuation, stream retry and resume. No separate player panel.
- Automatic system light/dark themes across surfaces, menus, forms and logo.
- Original logo transitions to seven real FFT frequency bands during playback,
  using the same audio element; it returns on pause/buffering and respects reduced motion.
- Queue editing, saved items, listening history, finished/restart actions,
  local persistence and export/import/clear controls.
- Async API integration, bounded caches/concurrency, timeouts, rate limiting,
  input validation, controlled errors and health/status endpoints.
- Range-aware streaming, bounded resolution/stream capacity, stale-source retry,
  strict source validation and disconnect cleanup.
- Production Node/Uvicorn containers, Compose configuration and CI checks.

## Verified

| Check | Evidence |
| --- | --- |
| Backend tests | 91 passed locally on Python 3.13 and inside production Python 3.11 container. |
| Frontend unit tests | 50 passed on Node 22. |
| Svelte check | 0 errors and 0 warnings. |
| Production build | SvelteKit Node-adapter build passed. |
| Browser integration | 38 passed across desktop and mobile Chrome emulation, including actual FFT tone discrimination, silence, pause/resume and unsupported-Web-Audio fallback. |
| Browser coverage | Search, channels, queue, saved/history, export/import, paused resume, media actions, speed, seek, sleep end, automatic continuation, failure/retry, rapid source changes and autoplay denial. |
| Live upstream audio | Public video resolved; audio/mp4 returned HTTP206 with requested bytes 0-1023. |
| Running API audio | `/audio/{id}` returned HTTP206 and bytes 0-1023/309288 through the application. |
| Live browser audio | Chrome played actual upstream audio and advanced its playback time. |
| Container builds | Backend and frontend production images built. |
| Container smoke | API /health and frontend HTML returned HTTP200 through host ports; Node22, resolver dependencies, CORS and runtime API URL verified. Verification containers removed afterward. |
| Compose | Configuration validated against example environment values. |
| Visual inspection | Desktop and mobile screenshots inspected; no horizontal overflow or unexpected browser console errors in fixture-filled UI. |

Browser tests use mocked metadata and generated PCM audio. They exercise the native
media pipeline but do not represent actual iOS/Android background behavior. Live
provider tests used one short public item and are a smoke test, not a guarantee
that every video is playable. Scripted tab switching and a requested window minimize continued audio, but left
document visibility reported as visible; these checks do not establish hidden-page
behavior.

## Required device checks before calling background listening complete

- [ ] On the intended iPhone/Safari and/or Android/Chrome device, start an item,
      switch to another application for several minutes, and confirm continuous audio.
- [ ] Lock the device and confirm continuous playback and working lock-screen controls.
- [ ] Verify queue continuation while locked, including an expired source refresh.
- [ ] Check headset controls, interruption/resume after a call, and reconnect after
      Wi-Fi/mobile-data changes.
- [ ] Confirm sleep-timer behavior on the target OS; suspended browser timers can
      delay a scheduled stop.
- [ ] Run a sustained listening session with the intended long-form content.

## Deployment checks

- [ ] Select hosting and configure HTTPS, origins, API key storage, streaming-compatible
      proxy timeouts, capacity/bandwidth limits and monitoring.
- [ ] Run CI on the actual remote branch; this file records local checks only.
- [ ] Deploy both services and verify search/play/seek/resume against that deployment.
- [ ] Repeat physical-device background checks against the hosted origin.

## Rollback

Keep the previous service images/deployment available. Roll back both frontend and
API together if their contracts differ. The frontend's library JSON uses version 1;
export it before testing an older client against the same browser storage. No
external service state or database migration was applied by this implementation.
