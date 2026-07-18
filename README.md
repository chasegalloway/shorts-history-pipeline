# Faceless Shorts Channel — Dark History & Disasters

Fully automated YouTube Shorts pipeline: topic queue → Claude-written script →
Kokoro voiceover → Whisper word captions → archival images (Wikimedia Commons) →
ffmpeg 9:16 render → YouTube API upload with scheduled publish.

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

## Automation

`setup_task.ps1` registers a Windows Task Scheduler job that runs
`python -m pipeline.run --count 1` twice daily (10:00 and 17:00).

## Policy guardrails (why this isn't "mass-produced content")

- Every script is a unique researched story with a rotating hook archetype;
  recent titles are fed back into the prompt to prevent repetition.
- `containsSyntheticMedia` disclosure is set on every upload.
- Image licenses are verified (public domain / CC) and credited in descriptions.
- Review `output/` regularly — quality drift is the biggest channel risk.
