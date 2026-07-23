"""Post one already-rendered video from the DB to TikTok by id prefix.

Makes overnight-batch TikTok failures replayable without re-rendering.

Usage: python tiktok_one.py 20260717-the-aberfan-disaster
"""
import json
import sys
from pathlib import Path

from pipeline import db, tiktok
from pipeline.common import load_config
from pipeline.run import build_hashtags

vid_prefix = sys.argv[1]
cfg = load_config()
con = db.connect()
row = con.execute(
    "SELECT * FROM videos WHERE id LIKE ? AND tiktok_status != 'posted'", (vid_prefix + "%",)
).fetchone()
if row is None:
    raise SystemExit(f"no un-posted video matching {vid_prefix!r}")

caption = row["yt_title"] + "\n\n" + build_hashtags(json.loads(row["tags"]))
publish_id = tiktok.post(Path(row["file"]), caption, cfg)
con.execute(
    "UPDATE videos SET tiktok_status='posted', tiktok_id=? WHERE id=?",
    (publish_id, row["id"]),
)
con.commit()
print(f"POSTED {row['id']} -> TikTok publish_id={publish_id}")
