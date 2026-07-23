"""Post one already-rendered video from the DB to TikTok by id prefix.

Makes overnight-batch TikTok failures replayable without re-rendering. Reuses the
video's YouTube publish slot so the retry still lands with the YT post; pass --now
to post immediately instead (needed once that slot is inside TikTok's scheduling
floor or already past).

Usage: python tiktok_one.py 20260717-the-aberfan-disaster [--now]
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import db
from pipeline.common import load_config
from pipeline.run import build_hashtags, cross_post_tiktok

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    raise SystemExit(__doc__)
vid_prefix, post_now = args[0], "--now" in sys.argv

cfg = load_config()
con = db.connect()
row = con.execute(
    "SELECT * FROM videos WHERE id LIKE ? AND tiktok_status != 'posted'", (vid_prefix + "%",)
).fetchone()
if row is None:
    raise SystemExit(f"no un-posted video matching {vid_prefix!r}")

slot = None
if row["publish_at"] and not post_now:
    slot = datetime.fromisoformat(row["publish_at"]).astimezone(
        ZoneInfo(cfg["channel"]["timezone"]))

caption = row["yt_title"] + "\n\n" + build_hashtags(json.loads(row["tags"]))
publish_id = cross_post_tiktok(Path(row["file"]), caption, slot, cfg)
con.execute(
    "UPDATE videos SET tiktok_status='posted', tiktok_id=? WHERE id=?",
    (publish_id, row["id"]),
)
con.commit()
print(f"POSTED {row['id']} -> TikTok {publish_id}")
