"""Orchestrator: one invocation produces (and uploads) N finished shorts.

Usage:
    python -m pipeline.run --seed                # load data/topics_seed.json into DB
    python -m pipeline.run --dry-run             # render 1 video, no upload
    python -m pipeline.run --count 2             # produce + upload 2 videos
"""
import argparse
import json
import re
from datetime import datetime, timezone

from . import analytics, assemble, captions, db, script_gen, tts, upload, visuals
from .common import ROOT, get_logger, load_config, out_dir

log = get_logger("run")

HASHTAGS = "#shorts #history #darkhistory #truestory #documentary"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def title_filename(title: str) -> str:
    """Catchy YT title as a Windows-safe filename (Studio pre-fills title from it)."""
    name = "".join(c for c in title if c not in '<>:"/\\|?*').strip().rstrip(".")
    return (name or "untitled")[:120]


def produce_one(con, cfg, dry_run: bool, weights: dict | None = None) -> bool:
    weights = weights or {}
    topic = db.next_topic(con, weights.get("tier"))
    if topic is None:
        log.error("Topic queue is empty — add more topics to data/topics_seed.json and --seed")
        return False

    video_id = f"{datetime.now().strftime('%Y%m%d')}-{slugify(topic['title'])}"
    workdir = out_dir(cfg, video_id)
    log.info("=== Producing %s ===", video_id)

    try:
        meta = script_gen.generate(
            dict(topic) | {"search_terms": json.loads(topic["search_terms"])},
            db.recent_titles(con), db.recent_hooks(con), cfg,
            hook_weights=weights.get("hook"),
        )

        # display form goes to captions/records; phonetic respellings go to TTS
        display_script, spoken_script = script_gen.split_script(meta["script"])
        meta["script"] = display_script

        voice_wav = workdir / "voice.wav"
        tts.synthesize(spoken_script, voice_wav, cfg)

        words = captions.transcribe_words(voice_wav, cfg)
        words = captions.correct_words(words, display_script)
        ass_file = captions.build_ass(words, workdir / "captions.ass", cfg)

        seed_terms = json.loads(topic["search_terms"])
        terms = seed_terms + [t for t in meta["image_search_terms"] if t not in seed_terms]
        images, attributions = visuals.gather_images(terms, workdir / "images", cfg)

        out_mp4 = workdir / f"{title_filename(meta['title'])}.mp4"
        duration = assemble.assemble(images, voice_wav, ass_file, out_mp4, cfg)

        description = meta["description"].strip() + "\n\n" + HASHTAGS
        if attributions:
            description += "\n\nImage credits:\n" + "\n".join(attributions[:10])

        rec = {
            "id": video_id,
            "topic_id": topic["id"],
            "hook_type": meta["hook_type"],
            "script": meta["script"],
            "yt_title": meta["title"],
            "yt_description": description,
            "tags": json.dumps(meta["tags"]),
            "file": str(out_mp4),
            "duration_s": duration,
            "upload_status": "dry_run" if dry_run else "pending",
            "youtube_id": None,
            "publish_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # persist record BEFORE upload: a failed upload stays resumable via upload_one.py
        db.save_video(con, rec)
        db.mark_topic(con, topic["id"], "used")
        (workdir / "metadata.json").write_text(
            json.dumps(rec | {"attributions": attributions}, indent=2), encoding="utf-8")

        if not dry_run and cfg["upload"]["enabled"]:
            taken = [r["publish_at"] for r in con.execute(
                "SELECT publish_at FROM videos WHERE publish_at IS NOT NULL").fetchall()]
            slot = upload.next_publish_slot(cfg, taken)
            rec["youtube_id"] = upload.upload(
                out_mp4, {"yt_title": meta["title"], "yt_description": description,
                          "tags": meta["tags"]}, slot, cfg)
            rec["upload_status"] = "uploaded"
            rec["publish_at"] = slot.astimezone(timezone.utc).isoformat()
            db.save_video(con, rec)
            (workdir / "metadata.json").write_text(
                json.dumps(rec | {"attributions": attributions}, indent=2), encoding="utf-8")
        log.info("=== Done: %s (%s) ===", video_id, rec["upload_status"])
        return True
    except Exception:
        log.exception("Production failed for %s", video_id)
        db.mark_topic(con, topic["id"], "failed")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="render only, skip upload")
    ap.add_argument("--seed", action="store_true", help="seed topic queue from data/topics_seed.json")
    ap.add_argument("--stats", action="store_true", help="refresh analytics stats and exit")
    args = ap.parse_args()

    cfg = load_config()
    con = db.connect()
    if args.seed:
        added = db.seed_topics(con, ROOT / "data" / "topics_seed.json")
        log.info("Seeded %d new topics", added)
        return
    if args.stats:
        analytics.refresh_stats(con, cfg)
        analytics.hook_weights(con)
        analytics.tier_weights(con)
        return

    # analytics feedback loop: refresh performance data, derive selection weights;
    # degrade gracefully (cold start, missing scope, API not enabled)
    weights = {}
    if not args.dry_run:
        try:
            analytics.refresh_stats(con, cfg)
        except Exception as e:
            log.warning("Analytics refresh unavailable (%s) — using cold-start rotation", e)
    try:
        weights = {"hook": analytics.hook_weights(con) or None,
                   "tier": analytics.tier_weights(con) or None}
    except Exception as e:
        log.warning("Weight computation failed (%s)", e)

    ok = 0
    for _ in range(args.count):
        if produce_one(con, cfg, args.dry_run, weights):
            ok += 1
    log.info("Produced %d/%d videos", ok, args.count)
    if ok < args.count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
