"""YouTube Data API upload with OAuth, synthetic-media disclosure, and
staggered publishAt scheduling.

Requires secrets/client_secret.json (OAuth desktop credentials from the
Google Cloud project). First run opens a browser for consent; the token is
cached at secrets/token.json.

NOTE: until the Cloud project passes YouTube's API audit, uploaded videos are
locked private regardless of settings — flip them public in YouTube Studio.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import ROOT, get_logger

log = get_logger("upload")

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPES = [UPLOAD_SCOPE, ANALYTICS_SCOPE]
SECRETS = ROOT / "secrets"


class MissingScopeError(Exception):
    """Cached token lacks a required scope; interactive re-auth needed."""


def get_credentials(require: str = UPLOAD_SCOPE, allow_flow: bool = True):
    """Load cached credentials WITH THEIR OWN granted scopes (never force new
    scopes onto an old token — Google rejects the refresh with invalid_scope,
    which would break uploads too). If the required scope is missing, either
    run the interactive consent flow (allow_flow) or raise MissingScopeError
    so unattended callers can degrade gracefully."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_file = SECRETS / "token.json"
    client_file = SECRETS / "client_secret.json"
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file))
    granted = set(creds.scopes or []) if creds else set()
    if creds is None or require not in granted:
        if not allow_flow:
            raise MissingScopeError(f"token lacks scope {require}; run reauth.py")
        if not client_file.exists():
            raise FileNotFoundError(
                f"Missing {client_file}. Create OAuth desktop credentials in the "
                "Google Cloud console and save them there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
        creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    elif not creds.valid:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_service():
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=get_credentials())


def next_publish_slot(cfg: dict, taken: list[str]) -> datetime:
    """Next unclaimed publish time from config, at least 2h from now."""
    tz = ZoneInfo(cfg["channel"]["timezone"])
    now = datetime.now(tz)
    taken_set = set(taken)
    for day_offset in range(0, 14):
        day = (now + timedelta(days=day_offset)).date()
        for hhmm in cfg["upload"]["publish_times"]:
            h, m = map(int, hhmm.split(":"))
            slot = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if slot < now + timedelta(hours=2):
                continue
            iso = slot.astimezone(timezone.utc).isoformat()
            if iso not in taken_set:
                return slot
    return now + timedelta(hours=2)


def upload(video_file: Path, meta: dict, publish_at: datetime, cfg: dict) -> str:
    from googleapiclient.http import MediaFileUpload

    service = get_service()
    body = {
        "snippet": {
            "title": meta["yt_title"][:100],
            "description": meta["yt_description"],
            "tags": meta["tags"][:15],
            "categoryId": cfg["upload"]["category_id"],
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": cfg["upload"]["privacy"],
            "publishAt": publish_at.astimezone(timezone.utc).isoformat(),
            "selfDeclaredMadeForKids": cfg["upload"]["made_for_kids"],
            "containsSyntheticMedia": True,
        },
        "paidProductPlacementDetails": {
            "hasPaidProductPlacement": bool(cfg["upload"].get("paid_promotion", False)),
        },
    }
    media = MediaFileUpload(str(video_file), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    request = service.videos().insert(
        part="snippet,status,paidProductPlacementDetails", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Upload %d%%", int(status.progress() * 100))
    vid = response["id"]
    log.info("Uploaded https://youtube.com/shorts/%s (publishAt=%s)", vid, publish_at.isoformat())
    return vid
