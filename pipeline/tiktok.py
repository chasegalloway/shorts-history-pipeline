"""TikTok Content Posting API (Direct Post) with Login Kit OAuth.

Requires secrets/tiktok_client.json holding {"client_key","client_secret"} from
the app at developers.tiktok.com (Login Kit + Content Posting API products, with
the video.publish scope). First auth runs an interactive consent flow
(tiktok_auth.py) and caches secrets/tiktok_token.json; the access token (24h) is
refreshed unattended via the refresh token (365d).

NOTE: until the TikTok app passes audit, every post is forced to SELF_ONLY
(private) regardless of config — creator_info reports the allowed privacy levels
and we clamp to them. Flip config tiktok.privacy_level to PUBLIC_TO_EVERYONE once
audited; no code change needed.

Direct Post publishes immediately — TikTok has no publishAt equivalent, so posts
go out at production time rather than on YouTube's staggered slots.
"""
import json
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .common import ROOT, get_logger

log = get_logger("tiktok")

API = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "video.publish,user.info.basic"
REDIRECT_PORT = 8723
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SECRETS = ROOT / "secrets"
CLIENT_FILE = SECRETS / "tiktok_client.json"
TOKEN_FILE = SECRETS / "tiktok_token.json"


def _load_client() -> dict:
    if not CLIENT_FILE.exists():
        raise FileNotFoundError(
            f"Missing {CLIENT_FILE}. Create a TikTok app (Login Kit + Content "
            'Posting API), then save {"client_key":..., "client_secret":...} there.'
        )
    return json.loads(CLIENT_FILE.read_text(encoding="utf-8"))


def _save_token(tok: dict) -> None:
    # stamp an absolute expiry so refresh decisions don't depend on fetch time
    tok = dict(tok)
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 0))
    TOKEN_FILE.write_text(json.dumps(tok, indent=2), encoding="utf-8")


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = (qs.get("code") or [None])[0]
        _CallbackHandler.error = (qs.get("error_description") or qs.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "TikTok authorization complete — you can close this tab."
        self.wfile.write(f"<html><body><h3>{msg}</h3></body></html>".encode())

    def log_message(self, *args):  # silence default stderr logging
        pass


def authorize() -> dict:
    """Interactive consent flow: opens a browser, captures the code on the local
    redirect, exchanges it for tokens, and caches them. Run via tiktok_auth.py."""
    client = _load_client()
    params = {
        "client_key": client["client_key"],
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": "faceless-channel",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    log.info("Opening browser for TikTok consent…")
    webbrowser.open(url)
    print(f"If no browser opened, visit:\n{url}")

    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    server.handle_request()  # blocks until the single redirect hits /callback
    if _CallbackHandler.error:
        raise RuntimeError(f"TikTok authorization failed: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise RuntimeError("No authorization code received from TikTok")

    resp = requests.post(
        f"{API}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client["client_key"],
            "client_secret": client["client_secret"],
            "code": _CallbackHandler.code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    if "access_token" not in tok:
        raise RuntimeError(f"Token exchange failed: {tok}")
    _save_token(tok)
    return tok


def _refresh(client: dict, tok: dict) -> dict:
    resp = requests.post(
        f"{API}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client["client_key"],
            "client_secret": client["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    new = resp.json()
    if "access_token" not in new:
        raise RuntimeError(f"Token refresh failed: {new}")
    # TikTok returns a fresh refresh_token too; keep whichever it hands back
    _save_token(new)
    return new


def access_token() -> str:
    """Return a valid access token, refreshing unattended when it's near expiry.
    Raises if no cached token exists — run tiktok_auth.py once first."""
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"Missing {TOKEN_FILE}. Run `python tiktok_auth.py` once to authorize."
        )
    client = _load_client()
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    # refresh with a 5-minute margin so an in-flight upload never trips expiry
    if time.time() >= float(tok.get("expires_at", 0)) - 300:
        tok = _refresh(client, tok)
    return tok["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


def creator_info(token: str) -> dict:
    """Required before Direct Post. Returns allowed privacy levels + interaction
    toggle limits for this creator."""
    resp = requests.post(
        f"{API}/v2/post/publish/creator_info/query/", headers=_headers(token), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"creator_info error: {data['error']}")
    return data["data"]


def _pick_privacy(cfg_level: str, allowed: list[str]) -> str:
    """Use the configured level if the (audit-gated) options allow it, else fall
    back to the first allowed — keeps unaudited posts legal and flips to public
    automatically once audit widens the options."""
    if cfg_level in allowed:
        return cfg_level
    if allowed:
        log.warning("privacy_level %s not allowed (unaudited?); using %s",
                    cfg_level, allowed[0])
        return allowed[0]
    raise RuntimeError("creator_info returned no privacy_level_options")


def post(video_file: Path, caption: str, cfg: dict) -> str:
    """Direct-post a video: creator_info → init (FILE_UPLOAD) → PUT bytes → poll.
    Returns the publish_id. Publishes immediately (no scheduling)."""
    tc = cfg["tiktok"]
    token = access_token()

    info = creator_info(token)
    privacy = _pick_privacy(tc.get("privacy_level", "SELF_ONLY"),
                            info.get("privacy_level_options", []))

    size = video_file.stat().st_size
    # A ~65s Short is well under the 64MB single-chunk ceiling → one chunk.
    init_body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": privacy,
            "disable_comment": bool(tc.get("disable_comment", False)),
            "disable_duet": bool(tc.get("disable_duet", False)),
            "disable_stitch": bool(tc.get("disable_stitch", False)),
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        },
    }
    if tc.get("disclose_commercial", False):
        init_body["post_info"]["brand_content_toggle"] = True

    resp = requests.post(f"{API}/v2/post/publish/video/init/",
                         headers=_headers(token), json=init_body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"init error: {data['error']}")
    publish_id = data["data"]["publish_id"]
    upload_url = data["data"]["upload_url"]

    # single-chunk PUT: whole file in one range
    with open(video_file, "rb") as f:
        body = f.read()
    put = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Content-Length": str(size),
        },
        data=body,
        timeout=300,
    )
    put.raise_for_status()
    log.info("Uploaded bytes for publish_id=%s, polling status…", publish_id)

    return _await_publish(token, publish_id, privacy)


def _await_publish(token: str, publish_id: str, privacy: str, timeout_s: int = 300) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = requests.post(
            f"{API}/v2/post/publish/status/fetch/",
            headers=_headers(token), json={"publish_id": publish_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        status = data.get("status")
        if status == "PUBLISH_COMPLETE":
            visibility = "private (SELF_ONLY)" if privacy == "SELF_ONLY" else privacy
            log.info("TikTok publish complete: %s (%s)", publish_id, visibility)
            return publish_id
        if status in ("FAILED", "FAILED_MODERATION"):
            raise RuntimeError(f"TikTok publish {status}: {data.get('fail_reason')}")
        time.sleep(5)
    raise TimeoutError(f"TikTok publish {publish_id} did not complete in {timeout_s}s")
