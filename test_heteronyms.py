"""Quick check: pronunciation markup -> TTS speaks respelling, captions show display form."""
from pathlib import Path

from pipeline.captions import build_ass, correct_words, transcribe_words
from pipeline.common import load_config
from pipeline.script_gen import split_script
from pipeline.tts import synthesize

script = ("The cans were sealed with lead[led], and every sailor who ate from them "
          "grew weaker by the minute. The crew of the Vajont[vye-ONT] expedition "
          "could only bow[bau] to the wind and wait.")
display, spoken = split_script(script)
print("DISPLAY:", display)
print("SPOKEN: ", spoken)
assert "lead," in display and "led," in spoken and "Vajont" in display

cfg = load_config()
out = Path("output/_test")
wav = synthesize(spoken, out / "het.wav", cfg)
words = transcribe_words(wav, cfg)
print("WHISPER:", " ".join(w["text"] for w in words))
words = correct_words(words, display)
corrected = " ".join(w["text"] for w in words)
print("CORRECTED:", corrected)
build_ass(words, out / "het.ass", cfg)
assert "lead" in corrected.lower().split(",")[0] or " lead" in corrected.lower()
print("OK")
