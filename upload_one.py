"""Upload one already-rendered video from the DB by id prefix.

Usage: python upload_one.py 20260717-the-aberfan-disaster
"""
import json
import sys
from datetime import timezone
from pathlib import Path

from pipeline import db, upload
from pipeline.common import load_config

vid_prefix = sys.argv[1]
cfg = load_config()
con = db.connect()
row = con.execute(
    "SELECT * FROM videos WHERE id LIKE ? AND upload_status != 'uploaded'", (vid_prefix + "%",)
).fetchone()
if row is None:
    raise SystemExit(f"no un-uploaded video matching {vid_prefix!r}")

taken = [r["publish_at"] for r in con.execute(
    "SELECT publish_at FROM videos WHERE publish_at IS NOT NULL").fetchall()]
slot = upload.next_publish_slot(cfg, taken)
meta = {
    "yt_title": row["yt_title"],
    "yt_description": row["yt_description"],
    "tags": json.loads(row["tags"]),
}
yt_id = upload.upload(Path(row["file"]), meta, slot, cfg)
con.execute(
    "UPDATE videos SET upload_status='uploaded', youtube_id=?, publish_at=? WHERE id=?",
    (yt_id, slot.astimezone(timezone.utc).isoformat(), row["id"]),
)
con.commit()
print(f"UPLOADED {row['id']} -> https://youtube.com/shorts/{yt_id} publishAt={slot}")
