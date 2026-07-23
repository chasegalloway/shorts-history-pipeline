# Faceless Shorts Channel — Dark History & Disasters

Fully automated YouTube Shorts pipeline: topic queue → Claude-written script →
Kokoro voiceover → Whisper word captions → archival images (Wikimedia Commons) →
ffmpeg 9:16 render → YouTube API upload with scheduled publish, and TikTok
cross-post via the Content Posting API.

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

### TikTok cross-post (optional, `tiktok.enabled` in config.yaml)

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
5. TikTok Direct Post publishes **immediately** (no scheduled publish time), so
   TikTok posts go out when the batch runs. `python tiktok_one.py <video-id>`
   re-posts an already-rendered video after a failure.

## Automation

`setup_task.ps1` registers a Windows Task Scheduler job that runs
`python -m pipeline.run --count 1` twice daily (10:00 and 17:00).

## Policy guardrails (why this isn't "mass-produced content")

- Every script is a unique researched story with a rotating hook archetype;
  recent titles are fed back into the prompt to prevent repetition.
- `containsSyntheticMedia` disclosure is set on every upload.
- Image licenses are verified (public domain / CC) and credited in descriptions.
- Review `output/` regularly — quality drift is the biggest channel risk.
