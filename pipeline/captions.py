"""Word-level captions: faster-whisper timestamps -> burned-in ASS subtitles."""
import difflib
import re
from pathlib import Path

from .common import get_logger

log = get_logger("captions")

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,7,0,2,60,60,620,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_whisper_model = None


def _ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def transcribe_words(wav: Path, cfg: dict) -> list[dict]:
    global _whisper_model
    from faster_whisper import WhisperModel

    w = cfg["whisper"]
    if _whisper_model is None:
        log.info("Loading faster-whisper %s (%s/%s)...", w["model"], w["device"], w["compute_type"])
        _whisper_model = WhisperModel(w["model"], device=w["device"], compute_type=w["compute_type"])
    segments, _ = _whisper_model.transcribe(str(wav), word_timestamps=True, language="en")
    words = []
    for seg in segments:
        for word in seg.words or []:
            words.append({"text": word.word.strip(), "start": word.start, "end": word.end})
    log.info("Transcribed %d words from %s", len(words), wav.name)
    return words


def correct_words(words: list[dict], display_text: str) -> list[dict]:
    """Replace whisper's transcribed spellings with the script's written form.

    Whisper hears the spoken (phonetic) text, so heteronym respellings and
    garbled proper nouns would otherwise leak into captions. Aligning the
    transcript to the display script keeps whisper's timing but the script's
    spelling wherever the two runs line up.
    """
    script_tokens = display_text.split()

    def norm(w: str) -> str:
        return re.sub(r"[^a-z0-9']", "", w.lower())

    sm = difflib.SequenceMatcher(
        a=[norm(w["text"]) for w in words],
        b=[norm(t) for t in script_tokens],
        autojunk=False,
    )
    fixed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("equal", "replace") and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                token = script_tokens[j1 + k]
                if words[i1 + k]["text"] != token:
                    fixed += 1
                words[i1 + k]["text"] = token
    if fixed:
        log.info("Corrected %d caption words to script spelling", fixed)
    return words


def build_ass(words: list[dict], out_ass: Path, cfg: dict) -> Path:
    """Group words into short chunks that pop in, shorts-style."""
    cap = cfg["captions"]
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        too_many = len(cur) >= cap["max_words_per_chunk"]
        too_long = cur[-1]["end"] - cur[0]["start"] > 1.4
        ends_clause = w["text"] and w["text"][-1] in ".,!?;:"
        if too_many or too_long or ends_clause:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    lines = [ASS_HEADER.format(font=cap["font"], size=cap["font_size"])]
    for i, ch in enumerate(chunks):
        start = ch[0]["start"]
        # hold until the next chunk starts so captions never flicker off
        end = chunks[i + 1][0]["start"] if i + 1 < len(chunks) else ch[-1]["end"] + 0.6
        text = " ".join(w["text"] for w in ch).upper()
        text = text.replace("{", "").replace("}", "")
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,"
            f"{{\\fad(60,0)\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}}{text}\n"
        )
    out_ass.write_text("".join(lines), encoding="utf-8")
    log.info("Captions: %d chunks -> %s", len(chunks), out_ass.name)
    return out_ass
