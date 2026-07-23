"""Generate a channel performance report from local analytics.

Refreshes stats from the YouTube Analytics API (degrades gracefully if the API
is unavailable or the audit isn't cleared), then writes a Markdown breakdown to
output/reports/<date>.md: totals, top/bottom performers by retention and views,
and cuts by hook archetype and topic tier.

Usage: python analytics_report.py            # refresh + write report
       python analytics_report.py --no-refresh  # use stats already in the DB
"""
import argparse
from datetime import datetime, timezone
from statistics import mean

from pipeline import analytics, db
from pipeline.common import ROOT, load_config, get_logger

log = get_logger("report")


def _fmt_pct(x):
    return f"{x:.0f}%" if x is not None else "—"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def build_report(con) -> str:
    now = datetime.now(timezone.utc)
    # videos that actually have analytics
    rows = con.execute(
        """SELECT v.id, v.yt_title, v.hook_type, t.tier,
                  s.views, s.avg_view_pct, s.likes, s.shares, s.subs_gained
           FROM videos v
           LEFT JOIN topics t ON t.id = v.topic_id
           JOIN video_stats s ON s.video_id = v.id
           ORDER BY s.views DESC"""
    ).fetchall()

    total_videos = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    uploaded = con.execute("SELECT COUNT(*) FROM videos WHERE upload_status='uploaded'").fetchone()[0]
    first = con.execute("SELECT MIN(created_at) FROM videos").fetchone()[0]

    L = [f"# Channel report — {now.date().isoformat()}", ""]
    if first:
        age_days = (now - datetime.fromisoformat(first)).days
        L.append(f"Running **{age_days} days** (first video {first[:10]}). "
                 f"**{total_videos}** produced, **{uploaded}** uploaded, "
                 f"**{len(rows)}** with analytics data.")
    L.append("")

    if not rows:
        L.append("_No analytics yet — videos are private/audit-locked or the API "
                 "data hasn't populated. Flip videos public and re-run._")
        return "\n".join(L)

    views = [r["views"] or 0 for r in rows]
    pcts = [r["avg_view_pct"] for r in rows if r["avg_view_pct"] is not None]
    L += ["## Totals (public/tracked videos)", ""]
    L.append(f"- Total views: **{sum(views):,}**")
    L.append(f"- Total likes: **{sum(r['likes'] or 0 for r in rows):,}**")
    L.append(f"- Subs gained: **{sum(r['subs_gained'] or 0 for r in rows):,}**")
    L.append(f"- Median views/video: **{sorted(views)[len(views)//2]:,}**")
    L.append(f"- Avg retention: **{_fmt_pct(mean(pcts) if pcts else None)}**")
    L.append("")

    # Top performers by views
    L += ["## Top 10 by views", "",
          _table(["Title", "Views", "Retention", "Likes", "Subs"],
                 [[(r["yt_title"] or r["id"])[:48], f"{r['views'] or 0:,}",
                   _fmt_pct(r["avg_view_pct"]), r["likes"] or 0, r["subs_gained"] or 0]
                  for r in rows[:10]]), ""]

    # Best & worst by retention (needs a floor of views to be meaningful)
    ranked = sorted([r for r in rows if (r["views"] or 0) >= 20 and r["avg_view_pct"] is not None],
                    key=lambda r: r["avg_view_pct"], reverse=True)
    if ranked:
        L += ["## Retention leaders (≥20 views)", "",
              _table(["Title", "Retention", "Views"],
                     [[(r["yt_title"] or r["id"])[:48], _fmt_pct(r["avg_view_pct"]), f"{r['views'] or 0:,}"]
                      for r in ranked[:5]]), ""]
        # only split out laggards once the sample is big enough to not overlap the leaders
        if len(ranked) >= 12:
            L += ["## Retention laggards (≥20 views)", "",
                  _table(["Title", "Retention", "Views"],
                         [[(r["yt_title"] or r["id"])[:48], _fmt_pct(r["avg_view_pct"]), f"{r['views'] or 0:,}"]
                          for r in ranked[-5:]]), ""]

    # By hook archetype
    def group(key_fn, label):
        g = {}
        for r in rows:
            k = key_fn(r)
            if k is None:
                continue
            g.setdefault(k, []).append(r)
        tbl = []
        for k, rs in sorted(g.items(), key=lambda kv: -mean([x["views"] or 0 for x in kv[1]])):
            vv = [x["views"] or 0 for x in rs]
            pp = [x["avg_view_pct"] for x in rs if x["avg_view_pct"] is not None]
            tbl.append([k, len(rs), f"{mean(vv):,.0f}", _fmt_pct(mean(pp) if pp else None)])
        return _table([label, "n", "Avg views", "Avg retention"], tbl)

    L += ["## By hook archetype", "", group(lambda r: r["hook_type"], "Hook"), ""]
    L += ["## By topic tier (1 famous · 2 obscure · 3 mystery)", "",
          group(lambda r: r["tier"], "Tier"), ""]

    # Selection weights the pipeline will use
    try:
        hw = analytics.hook_weights(con)
        tw = analytics.tier_weights(con)
        L += ["## Current pipeline selection weights", ""]
        if hw:
            L.append("Hooks: " + ", ".join(f"{k} {v:.0%}" for k, v in
                     sorted(hw.items(), key=lambda kv: -kv[1])))
        if tw:
            L.append("Tiers: " + ", ".join(f"tier {k} {v:.0%}" for k, v in
                     sorted(tw.items(), key=lambda kv: -kv[1])))
        L.append("")
    except Exception as e:
        log.warning("weight computation failed: %s", e)

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-refresh", action="store_true", help="skip the API refresh")
    args = ap.parse_args()

    cfg = load_config()
    con = db.connect()
    if not args.no_refresh:
        try:
            n = analytics.refresh_stats(con, cfg)
            log.info("Refreshed %d videos", n)
        except Exception as e:
            log.warning("Analytics refresh unavailable (%s) — using stored stats", e)

    report = build_report(con)
    dest = ROOT / cfg["paths"]["output"] / "reports"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{datetime.now().date().isoformat()}.md"
    out.write_text(report, encoding="utf-8")
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
