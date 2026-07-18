"""Generate the script + metadata for one short via headless `claude -p`.

Anti-templating safeguards (YouTube "inauthentic content" policy):
- hook archetype rotates, avoiding whatever ran in the last few videos
- recent titles are passed in so phrasing/framing doesn't repeat
- prompt demands factual accuracy and forbids invented quotes/numbers
"""
import json
import random
import re
import shutil
import subprocess
import time

from .common import get_logger

log = get_logger("script_gen")

HOOK_ARCHETYPES = {
    "cold_open_moment": "Open mid-scene at the most dramatic second of the story, then rewind to explain.",
    "countdown": "Open with a time-stamp countdown to catastrophe (e.g. 'At 9:04 AM, the crew had 41 seconds left...').",
    "nobody_noticed": "Open with the tiny overlooked detail or mistake that caused everything ('No one noticed the...').",
    "survivor_pov": "Open from the point of view of a real named survivor or witness (only documented people).",
    "question_hook": "Open with a startling question the story answers ('Why did an entire town vanish overnight?').",
    "artifact_hook": "Open with a physical object that still exists today and work backward to its story.",
}

PROMPT_TEMPLATE = """You are the writer for a dark-history documentary YouTube Shorts channel. Write one script.

TOPIC: {title}
ANGLE: {angle}

HOOK ARCHETYPE (must follow): {hook_name} — {hook_desc}

HARD REQUIREMENTS:
- {words_min}-{words_max} words of spoken narration (about 55 seconds). Count carefully.
- Documentary tone: vivid, concrete, restrained. No hype words, no "insane/crazy/mind-blowing".
- STRICT factual accuracy. Only documented facts. Never invent quotes, names, numbers, or details. If a detail is uncertain, omit it or hedge ("reportedly").
- Structure: hook (first 2 sentences must create an open question), escalating tension, payoff/resolution, then ONE final line that invites reflection or comment — vary its wording, never "like and subscribe".
- Plain spoken prose only: no headings, emojis, stage directions, or camera notes. Write numbers as words where natural for speech.
- PRONUNCIATION MARKUP: for any word a text-to-speech engine is likely to mispronounce — heteronyms (lead the metal, wind a clock, tear, bow, wound, dove, row, bass, close, minute meaning tiny, desert, live) and tricky proper nouns (Worcester, Vajont, Kholat) — write the normal spelling followed by a phonetic respelling in square brackets: "lead[led] poisoning", "took a bow[bau]", "Vajont[vye-ONT] Dam". The bracket text is ONLY what gets spoken; the normal spelling is displayed. Use this only where mispronunciation is genuinely likely.
- Do not structurally or verbally resemble these recent videos: {recent_titles}

Also produce YouTube metadata:
- title: max 90 chars, curiosity-gap style but not clickbait-dishonest, no ALL CAPS words, no emoji
- description: 2-3 sentences summarizing the story + a line inviting discussion. Do not include hashtags (added automatically).
- tags: 8-12 relevant tags
- image_search_terms: 8-10 short search phrases for finding archival/public-domain photos of THIS story on Wikimedia Commons (specific: names, places, ships, dates — not generic moods)

Respond with ONLY a JSON object, no markdown fences, exactly these keys:
{{"script": "...", "title": "...", "description": "...", "tags": ["..."], "image_search_terms": ["..."]}}"""


def pick_hook(recent: list[str], weights: dict[str, float] | None = None) -> str:
    pool = [h for h in HOOK_ARCHETYPES if h not in recent[:3]] or list(HOOK_ARCHETYPES)
    if weights:
        w = [weights.get(h, 1 / len(HOOK_ARCHETYPES)) for h in pool]
        return random.choices(pool, weights=w, k=1)[0]
    return random.choice(pool)


def generate(topic: dict, recent_titles: list[str], recent_hooks: list[str], cfg: dict,
             hook_weights: dict[str, float] | None = None) -> dict:
    hook = pick_hook(recent_hooks, hook_weights)
    prompt = PROMPT_TEMPLATE.format(
        title=topic["title"],
        angle=topic["angle"],
        hook_name=hook,
        hook_desc=HOOK_ARCHETYPES[hook],
        words_min=cfg["script"]["words_min"],
        words_max=cfg["script"]["words_max"],
        recent_titles="; ".join(recent_titles) or "(none yet)",
    )
    claude = shutil.which("claude")
    if not claude:
        raise FileNotFoundError("claude CLI not found on PATH")
    log.info("Generating script for %r (hook=%s)", topic["title"], hook)
    proc = None
    for attempt in range(3):
        proc = subprocess.run(
            [claude, "-p", "--model", cfg["script"]["model"]],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            break
        log.warning("claude -p attempt %d failed (rc=%s): %s",
                    attempt + 1, proc.returncode, (proc.stderr or proc.stdout)[:300])
        time.sleep(15 * (attempt + 1))
    else:
        raise RuntimeError(f"claude -p failed after 3 attempts: {(proc.stderr or proc.stdout)[:500]}")
    data = _parse_json(proc.stdout)
    for key in ("script", "title", "description", "tags", "image_search_terms"):
        if key not in data:
            raise ValueError(f"script JSON missing key {key!r}: {proc.stdout[:300]}")
    words = len(data["script"].split())
    log.info("Script: %d words, title=%r", words, data["title"])
    if words > cfg["script"]["words_max"] + 30:
        raise ValueError(f"script too long ({words} words), would exceed 60s")
    data["hook_type"] = hook
    return data


HETERONYM_RE = re.compile(r"([\w'’-]+)\[([^\]]+)\]")


def split_script(script: str) -> tuple[str, str]:
    """'lead[led] pipes' -> display 'lead pipes', spoken 'led pipes'."""
    display = HETERONYM_RE.sub(r"\1", script)
    spoken = HETERONYM_RE.sub(r"\2", script)
    return display, spoken


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON in claude output: {text[:300]}")
    return json.loads(text[start : end + 1])
