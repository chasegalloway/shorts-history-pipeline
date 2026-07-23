# Faceless Shorts Channel — Dark History & Disasters

Fully automated YouTube Shorts pipeline: topic queue → Claude-written script →
Kokoro voiceover → Whisper word captions → archival images (Wikimedia Commons) →
ffmpeg 9:16 render → YouTube API upload with scheduled publish, and a TikTok
cross-post scheduled for the same moment via the logged-in TikTok Studio session.

> **[Privacy Policy](PRIVACY.md)** · **[Terms of Service](TERMS.md)**
>
> This application uses **YouTube API Services** to upload videos to the
> operator's own YouTube channel. Use of this tool is subject to the
> [YouTube Terms of Service](https://www.youtube.com/t/terms) and the
> [Google Privacy Policy](http://www.google.com/policies/privacy).

## Commands

```powershell
.venv\Scripts\python -m pipeline.run --seed        # load topic queue (run once / after adding topics)
.venv\Scripts\python -m pipeline.run --dry-run     # produce 1 video locally, no upload
.venv\Scripts\python -m pipeline.run --count 2     # produce + upload 2 videos
```

Output lands in `output/<video-id>/` (mp4 + metadata.json + working files).
State lives in `channel.db` (SQLite). Logs in `pipeline.log`.

## One-time setup (manual steps)

1. **Create the YouTube channel** (youtube.com → switch account → create channel).
2. **Google Cloud**: create a project → enable *YouTube Data API v3* →
   OAuth consent screen (External, add yourself as test user) →
   Credentials → *Create OAuth client ID* → type **Desktop app** →
   download JSON → save as `secrets/client_secret.json`.
3. First upload run opens a browser for consent; token is cached after that.
4. **Submit the YouTube API audit form** (search "YouTube API Services - Audit
   and Quota Extension Form"). Until it's approved, API-uploaded videos are
   **locked private** — flip them public/scheduled in YouTube Studio (~2 min/day).
5. Optional: drop music tracks (YouTube Audio Library) into `assets/music/`.
   If empty, the pipeline generates ambient drone beds automatically.
6. Optional: `setx PEXELS_API_KEY <key>` for stock-photo fallback.

### TikTok cross-post (`tiktok.enabled` / `tiktok.method` in config.yaml)

Two implementations. **`method: browser` is the default and the one in use.**

#### `method: browser` — TikTok Studio web (default)

`pipeline/tiktok_browser.py` drives the persistent Edge profile in
`browser-profile/`, which holds the real logged-in TikTok session. Because it's a
normal Web Studio upload it posts **publicly** and, unlike the API, it can
**schedule**: every cross-post is set to the same publish time the video's
YouTube slot got, so both platforms drop together.

- Needs Playwright (`uv pip install playwright`) and an installed Edge; it reuses
  the system browser via `channel: msedge`, so there is no browser to download.
- Nothing to authorize — but the profile must stay signed in. If it's ever signed
  out, open TikTok Studio in that profile (via the browser MCP) and log back in.
- Runs headed; TikTok blocks headless. Task Scheduler runs it in the logged-in
  session, so a browser window flashes up during the batch.
- Only one process may use the profile at a time. A leftover browser from a
  previous run is killed and retried automatically; a browser MCP session you have
  open is not — close it before running the pipeline by hand.
- TikTok's scheduler reaches 10 days out. A slot beyond that raises instead of
  posting at the wrong time (can't happen at 6 renders/day into 6 slots/day).
- `python tiktok_one.py <video-id>` re-posts an already-rendered video after a
  failure, reusing its YouTube slot; add `--now` to post immediately instead.
- Selector smoke test after a TikTok UI change — fills the whole form, then
  discards instead of posting:
  ```python
  tiktok_browser.post(Path("output/_test/test.mp4"), "caption", when, cfg, commit=False)
  ```

#### `method: api` — Content Posting API (parked)

Kept for when/if the developer app passes audit; until then every post is forced
SELF_ONLY, which is why the browser path exists.

1. **TikTok for Developers** (developers.tiktok.com): create an app → add the
   **Login Kit** and **Content Posting API** products → request the
   **`video.publish`** scope → set redirect URI `http://localhost:8723/callback`.
2. Save the app's key/secret as `secrets/tiktok_client.json`:
   `{"client_key": "...", "client_secret": "..."}`.
3. `python tiktok_auth.py` once → browser consent → token cached at
   `secrets/tiktok_token.json` (auto-refreshed thereafter; no browser on later runs).
4. Like YouTube, **unaudited apps post SELF_ONLY (private)**. The pipeline clamps
   to whatever `creator_info` allows and warns. Submit the app for audit, then set
   `tiktok.privacy_level: PUBLIC_TO_EVERYONE` in `config.yaml` — no code change.
5. TikTok Direct Post publishes **immediately** — there is no scheduled publish
   time in the API, so this method cannot stay in sync with the YouTube slot.

## Automation

`setup_task.ps1` registers a Windows Task Scheduler job that runs
`python -m pipeline.run --count 1` six times daily (23:00, 02:00, 05:00, 10:00,
14:00, 18:00), one video per run, to fill the six `upload.publish_times` slots.
Each video is uploaded to YouTube scheduled for its slot and cross-posted to
TikTok scheduled for the same moment.

## Policy guardrails (why this isn't "mass-produced content")

- Every script is a unique researched story with a rotating hook archetype;
  recent titles are fed back into the prompt to prevent repetition.
- `containsSyntheticMedia` disclosure is set on every upload.
- Image licenses are verified (public domain / CC) and credited in descriptions.
- Review `output/` regularly — quality drift is the biggest channel risk.
