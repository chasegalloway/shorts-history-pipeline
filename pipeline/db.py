"""SQLite state: topic queue, produced videos, dedupe history."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, load_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL UNIQUE,
    angle TEXT NOT NULL,
    search_terms TEXT NOT NULL,      -- json list, seeds for archival image search
    tier INTEGER NOT NULL DEFAULT 2, -- 1 famous, 2 obscure-shocking, 3 mystery
    status TEXT NOT NULL DEFAULT 'queued',  -- queued | used | failed | skipped
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS video_stats (
    video_id TEXT PRIMARY KEY REFERENCES videos(id),
    youtube_id TEXT,
    fetched_at TEXT,
    views INTEGER,
    avg_view_pct REAL,
    avg_view_duration REAL,
    likes INTEGER,
    shares INTEGER,
    subs_gained INTEGER
);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,             -- e.g. 20260718-halifax-explosion
    topic_id INTEGER REFERENCES topics(id),
    hook_type TEXT,
    script TEXT,
    yt_title TEXT,
    yt_description TEXT,
    tags TEXT,                       -- json list
    file TEXT,
    duration_s REAL,
    upload_status TEXT NOT NULL DEFAULT 'pending',  -- pending | uploaded | failed | dry_run
    youtube_id TEXT,
    publish_at TEXT,
    created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    cfg = load_config()
    con = sqlite3.connect(ROOT / cfg["paths"]["db"])
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def seed_topics(con: sqlite3.Connection, seed_file: Path) -> int:
    topics = json.loads(seed_file.read_text(encoding="utf-8"))
    added = 0
    for t in topics:
        try:
            con.execute(
                "INSERT INTO topics (title, angle, search_terms, tier) VALUES (?,?,?,?)",
                (t["title"], t["angle"], json.dumps(t["search_terms"]), t.get("tier", 2)),
            )
            added += 1
        except sqlite3.IntegrityError:
            pass  # already seeded
    con.commit()
    return added


def next_topic(con: sqlite3.Connection, tier_weights: dict[int, float] | None = None) -> sqlite3.Row | None:
    if tier_weights:
        # analytics-weighted tier pick (weights already include an exploration floor)
        import random
        tiers = list(tier_weights)
        preferred_tier = random.choices(tiers, weights=[tier_weights[t] for t in tiers], k=1)[0]
    else:
        # cold start: rotate tiers so the channel mixes famous / obscure / mystery
        used_count = con.execute("SELECT COUNT(*) FROM topics WHERE status='used'").fetchone()[0]
        preferred_tier = (used_count % 3) + 1
    row = con.execute(
        "SELECT * FROM topics WHERE status='queued' ORDER BY (tier != ?), RANDOM() LIMIT 1",
        (preferred_tier,),
    ).fetchone()
    return row


def mark_topic(con: sqlite3.Connection, topic_id: int, status: str) -> None:
    con.execute(
        "UPDATE topics SET status=?, used_at=? WHERE id=?",
        (status, datetime.now(timezone.utc).isoformat(), topic_id),
    )
    con.commit()


def recent_titles(con: sqlite3.Connection, n: int = 15) -> list[str]:
    rows = con.execute(
        "SELECT yt_title FROM videos WHERE yt_title IS NOT NULL ORDER BY created_at DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [r["yt_title"] for r in rows]


def recent_hooks(con: sqlite3.Connection, n: int = 6) -> list[str]:
    rows = con.execute(
        "SELECT hook_type FROM videos WHERE hook_type IS NOT NULL ORDER BY created_at DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [r["hook_type"] for r in rows]


def save_video(con: sqlite3.Connection, rec: dict) -> None:
    con.execute(
        """INSERT OR REPLACE INTO videos
           (id, topic_id, hook_type, script, yt_title, yt_description, tags, file,
            duration_s, upload_status, youtube_id, publish_at, created_at)
           VALUES (:id,:topic_id,:hook_type,:script,:yt_title,:yt_description,:tags,:file,
                   :duration_s,:upload_status,:youtube_id,:publish_at,:created_at)""",
        rec,
    )
    con.commit()
