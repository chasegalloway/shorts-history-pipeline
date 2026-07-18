"""Source archival images from Wikimedia Commons (public domain / CC),
with Pexels stock b-roll stills as an optional supplement.

Every image's license + attribution is captured and later appended to the
video description.
"""
import os
from pathlib import Path

import requests
from PIL import Image

from .common import get_logger

log = get_logger("visuals")

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "FacelessHistoryShorts/1.0 (chase.h.galloway21@gmail.com)"}

# Licenses safe to use with attribution in a monetized video
OK_LICENSE_PREFIXES = (
    "public domain", "pd", "cc0", "cc by", "cc-by", "cc by-sa", "cc-by-sa", "no restrictions",
)
BAD_LICENSE_MARKERS = ("cc by-nc", "cc-by-nc", "nc-", "fair use", "non-free")


def _license_ok(short_name: str) -> bool:
    s = short_name.lower().strip()
    if any(b in s for b in BAD_LICENSE_MARKERS):
        return False
    return any(s.startswith(p) for p in OK_LICENSE_PREFIXES)


def search_commons(term: str, limit: int = 8) -> list[dict]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {term}",
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "iiurlwidth": 1600,
    }
    try:
        r = requests.get(COMMONS_API, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
    except Exception as e:
        log.warning("Commons search failed for %r: %s", term, e)
        return []
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not _license_ok(lic):
            continue
        if info.get("width", 0) < 500 or info.get("height", 0) < 400:
            continue
        artist = meta.get("Artist", {}).get("value", "")
        # strip html from artist field
        import re
        artist = re.sub(r"<[^>]+>", "", artist).strip()
        results.append({
            "url": info.get("thumburl") or info.get("url"),
            "title": page.get("title", ""),
            "license": lic,
            "artist": artist[:120],
            "descriptionurl": info.get("descriptionurl", ""),
            "width": info.get("width"),
            "height": info.get("height"),
        })
    return results


def search_pexels(term: str, limit: int = 4) -> list[dict]:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": term, "per_page": limit, "orientation": "portrait"},
            headers={"Authorization": key},
            timeout=30,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
    except Exception as e:
        log.warning("Pexels search failed for %r: %s", term, e)
        return []
    return [{
        "url": p["src"]["large2x"],
        "title": p.get("alt", term),
        "license": "Pexels License",
        "artist": p.get("photographer", ""),
        "descriptionurl": p.get("url", ""),
    } for p in photos]


def gather_images(search_terms: list[str], dest: Path, cfg: dict) -> tuple[list[Path], list[str]]:
    """Download up to images_target usable images. Returns (paths, attribution lines)."""
    target = cfg["video"]["images_target"]
    minimum = cfg["video"]["images_min"]
    dest.mkdir(parents=True, exist_ok=True)
    seen_urls: set[str] = set()
    paths: list[Path] = []
    attributions: list[str] = []

    candidates: list[dict] = []
    for term in search_terms:
        candidates.extend(search_commons(term))
        if len(candidates) >= target * 3:
            break
    if len(candidates) < minimum:
        for term in search_terms[:3]:
            candidates.extend(search_pexels(term))

    for c in candidates:
        if len(paths) >= target:
            break
        if not c["url"] or c["url"] in seen_urls:
            continue
        seen_urls.add(c["url"])
        p = dest / f"img_{len(paths):02d}.jpg"
        if _download_image(c["url"], p):
            paths.append(p)
            src = c["descriptionurl"] or c["url"]
            credit = f" by {c['artist']}" if c.get("artist") else ""
            attributions.append(f"{c['title']}{credit} ({c['license']}) {src}")

    log.info("Gathered %d/%d images", len(paths), target)
    if len(paths) < minimum:
        raise RuntimeError(f"only found {len(paths)} usable images (need {minimum})")
    return paths, attributions


def _download_image(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        dest.write_bytes(r.content)
        # validate + convert to clean RGB jpeg (strips odd modes/exif)
        with Image.open(dest) as im:
            im = im.convert("RGB")
            im.save(dest, "JPEG", quality=92)
        return True
    except Exception as e:
        log.warning("download failed %s: %s", url[:80], e)
        dest.unlink(missing_ok=True)
        return False
