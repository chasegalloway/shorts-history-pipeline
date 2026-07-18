"""Analytics feedback loop: pull per-video performance from the YouTube
Analytics API and turn it into hook-archetype and topic-tier weights.

Scoring: each video scores log1p(views) * (0.5 + avg_view_pct/200) —
log damps viral outliers so one spike doesn't monopolize the queue, and the
retention multiplier (0.5–1.0) rewards videos people actually finish.
Weights are blended 50/50 with uniform so every hook/tier keeps getting
explored even when one dominates.
"""
import math
from datetime import date, datetime, timezone

from .common import get_logger

log = get_logger("analytics")

EXPLORATION = 0.5  # fraction of weight that stays uniform


def get_analytics_service():
    from googleapiclient.discovery import build

    from .upload import ANALYTICS_SCOPE, get_credentials

    # never pop a browser from an unattended scheduled run
    return build("youtubeAnalytics", "v2",
                 credentials=get_credentials(require=ANALYTICS_SCOPE, allow_flow=False))


def refresh_stats(con, cfg) -> int:
    """Fetch metrics for all uploaded videos; upsert into video_stats."""
    rows = con.execute(
        "SELECT id, youtube_id FROM videos WHERE youtube_id IS NOT NULL"
    ).fetchall()
    if not rows:
        return 0
    service = get_analytics_service()
    by_yt = {r["youtube_id"]: r["id"] for r in rows}
    yt_ids = list(by_yt)
    updated = 0
    for i in range(0, len(yt_ids), 50):
        chunk = yt_ids[i : i + 50]
        resp = service.reports().query(
            ids="channel==MINE",
            startDate="2026-01-01",
            endDate=date.today().isoformat(),
            metrics="views,averageViewDuration,averageViewPercentage,likes,shares,subscribersGained",
            dimensions="video",
            filters="video==" + ",".join(chunk),
        ).execute()
        for row in resp.get("rows", []):
            yt_id, views, avg_dur, avg_pct, likes, shares, subs = row
            con.execute(
                """INSERT OR REPLACE INTO video_stats
                   (video_id, youtube_id, fetched_at, views, avg_view_pct,
                    avg_view_duration, likes, shares, subs_gained)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (by_yt[yt_id], yt_id, datetime.now(timezone.utc).isoformat(),
                 views, avg_pct, avg_dur, likes, shares, subs),
            )
            updated += 1
    con.commit()
    log.info("Refreshed stats for %d videos", updated)
    return updated


def _video_score(views: int, avg_pct: float) -> float:
    return math.log1p(views or 0) * (0.5 + (avg_pct or 0) / 200)


def _blend(scores: dict[str, list[float]]) -> dict[str, float]:
    """Mean score per key -> normalized weights blended with uniform."""
    if not scores:
        return {}
    means = {k: sum(v) / len(v) for k, v in scores.items() if v}
    if not means or sum(means.values()) == 0:
        return {}
    total = sum(means.values())
    n = len(means)
    return {k: EXPLORATION / n + (1 - EXPLORATION) * (m / total) for k, m in means.items()}


def hook_weights(con) -> dict[str, float]:
    rows = con.execute(
        """SELECT v.hook_type, s.views, s.avg_view_pct
           FROM videos v JOIN video_stats s ON s.video_id = v.id
           WHERE v.hook_type IS NOT NULL"""
    ).fetchall()
    scores: dict[str, list[float]] = {}
    for r in rows:
        scores.setdefault(r["hook_type"], []).append(_video_score(r["views"], r["avg_view_pct"]))
    w = _blend(scores)
    if w:
        log.info("Hook weights: %s", {k: round(v, 3) for k, v in w.items()})
    return w


def tier_weights(con) -> dict[int, float]:
    rows = con.execute(
        """SELECT t.tier, s.views, s.avg_view_pct
           FROM videos v
           JOIN topics t ON t.id = v.topic_id
           JOIN video_stats s ON s.video_id = v.id"""
    ).fetchall()
    scores: dict[int, list[float]] = {}
    for r in rows:
        scores.setdefault(r["tier"], []).append(_video_score(r["views"], r["avg_view_pct"]))
    w = _blend(scores)
    if w:
        log.info("Tier weights: %s", {k: round(v, 3) for k, v in w.items()})
    return w
