"""Produce (and upload) ONE video for a specific, ad-hoc topic — bypassing the
queue. Reuses the full pipeline via run.produce_one.

Usage:
    python make_one.py path/to/topic.json            # produce + upload (private until audit)
    python make_one.py path/to/topic.json --dry-run  # render only, no upload

topic.json: {"title": ..., "angle": ..., "search_terms": [...], "tier": 3}
"""
import argparse
import json

from pipeline import db
from pipeline.common import load_config
from pipeline.run import produce_one

ap = argparse.ArgumentParser()
ap.add_argument("topic_json")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

spec = json.loads(open(args.topic_json, encoding="utf-8").read())
cfg = load_config()
con = db.connect()

# insert (or reuse) the topic so it gets a real id + dedupe history
con.execute(
    "INSERT OR IGNORE INTO topics (title, angle, search_terms, tier) VALUES (?,?,?,?)",
    (spec["title"], spec["angle"], json.dumps(spec["search_terms"]), spec.get("tier", 2)),
)
con.commit()
row = con.execute("SELECT * FROM topics WHERE title=?", (spec["title"],)).fetchone()

ok = produce_one(con, cfg, args.dry_run, weights={}, topic=row)
raise SystemExit(0 if ok else 1)
