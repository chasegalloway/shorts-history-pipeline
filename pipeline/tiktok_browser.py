"""TikTok cross-post by driving the logged-in browser profile (TikTok Studio web).

This is the automated equivalent of posting by hand: Playwright reuses the same
persistent Edge profile the browser MCP uses (`browser-profile/`), so it inherits
the real logged-in session and posts as a normal Web Studio upload. That sidesteps
the Content Posting API in tiktok.py, which is stuck at SELF_ONLY until the app
passes audit.

Unlike the API, Web Studio *can* schedule: we set the same publish time YouTube
got, so both platforms drop together. TikTok's scheduler only reaches 10 days out;
a slot past that raises rather than silently posting at the wrong time.

The profile is a single-writer resource, so nothing else may be driving it while
this runs; a leftover browser from a previous run is killed and retried once.
"""
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from .common import ROOT, get_logger

log = get_logger("tiktok_browser")

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"

# TikTok Studio scheduling window, from the calendar's own `valid` day range.
MIN_LEAD = timedelta(minutes=20)
MAX_LEAD = timedelta(days=10)

# Big enough for a ~70 MB Short over a slow uplink plus TikTok-side processing.
UPLOAD_TIMEOUT_MS = 15 * 60 * 1000


def _profile_dir(cfg: dict) -> Path:
    p = Path(cfg["tiktok"].get("browser_profile", "browser-profile"))
    return p if p.is_absolute() else ROOT / p


def _kill_stale(profile: Path) -> int:
    """Kill browser processes still holding our profile.

    Edge sometimes outlives context.close(), which would lock out the next
    scheduled run. Matching on the profile path keeps this scoped to this
    project's dedicated profile — Chase's own Edge windows are never touched."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile.name}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; 1 }"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True)
    return len(out.stdout.split())


def _launch(pw, cfg):
    profile = _profile_dir(cfg)
    if not profile.exists():
        raise FileNotFoundError(
            f"{profile} not found — the logged-in TikTok browser profile is required. "
            "Open TikTok Studio once via the browser MCP to create it."
        )

    def _open():
        return pw.chromium.launch_persistent_context(
            str(profile),
            channel=cfg["tiktok"].get("browser_channel", "msedge"),
            headless=bool(cfg["tiktok"].get("headless", False)),
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )

    try:
        return _open()
    except Exception as e:
        killed = _kill_stale(profile)
        log.warning("Profile was locked (%s); killed %d stale browser processes", e, killed)
        time.sleep(3)
        try:
            return _open()
        except Exception as e2:
            raise RuntimeError(
                f"Could not open the TikTok browser profile at {profile} ({e2}). "
                "Close any Edge window or MCP browser session using it and retry."
            ) from e2


def _dismiss_modal(page, label: str) -> bool:
    """Click a modal button by exact label if such a modal is on screen.

    Scoped to the floating-ui portal so "Discard" only ever means the modal's
    button, never the form's own discard-this-post button in the footer."""
    btn = page.locator("[data-floating-ui-portal]").get_by_role(
        "button", name=label, exact=True)
    if btn.count() and btn.first.is_visible():
        btn.first.click()
        # the overlay animates out and swallows clicks until it detaches
        try:
            page.wait_for_selector(".TUXModal-overlay", state="detached", timeout=10_000)
        except Exception:
            pass
        page.wait_for_timeout(500)
        return True
    return False


def _set_caption(page, caption: str) -> None:
    """Replace the filename-derived default caption in the DraftJS editor."""
    editor = page.locator('div[contenteditable="true"]').first
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    # type() fires real key events, which DraftJS needs; fill() silently no-ops here
    page.keyboard.type(caption, delay=8)
    # a trailing '#tag' leaves the hashtag suggestion popup open, which would
    # otherwise eat the next click
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    got = editor.inner_text().strip()
    if got.split()[:4] != caption.split()[:4]:
        raise RuntimeError(f"caption did not stick — editor shows {got[:80]!r}")


def _open_schedule(page) -> None:
    """Switch 'When to post' to Schedule, accepting the one-time consent modal."""
    label = page.locator('label:has(input[name="postSchedule"][value="schedule"])')
    for _ in range(3):
        label.click(force=True)
        page.wait_for_timeout(1000)
        # first-ever schedule on this account asks to store the video server-side
        if _dismiss_modal(page, "Allow"):
            continue
        if page.locator('input[name="postSchedule"][value="schedule"]').is_checked():
            return
    raise RuntimeError("could not switch TikTok upload to Schedule mode")


def _pick(page, selector: str, text: str, nth: int = 0) -> bool:
    """Click the nth picker option whose text is exactly `text`.

    Done as a DOM click rather than a real mouse click on purpose: the time
    columns are virtual scrollers whose options sit outside the viewport, so
    coordinate clicks land on whichever row happens to be scrolled into place."""
    clicked = page.evaluate(
        """([sel, txt, n]) => {
            const hits = [...document.querySelectorAll(sel)]
                .filter(e => e.textContent.trim() === txt);
            if (n >= hits.length) return false;
            hits[n].click();
            return true;
        }""",
        [selector, text, nth],
    )
    page.wait_for_timeout(200)
    return clicked


def _settle(page, index: int, want: str, timeout_ms: int = 15_000) -> bool:
    """Wait for the schedule input at `index` to read `want`.

    The time columns animate-scroll to the clicked option and report every value
    they pass through, so reading the input immediately after a click yields a
    midpoint (clicking 02 from 18 reports 11 on the way). Poll until it lands."""
    try:
        page.wait_for_function(
            """([i, w]) => document.querySelectorAll('.scheduled-picker input')[i]
                             .value.startsWith(w)""",
            arg=[index, want],
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def _set_time(page, when: datetime) -> None:
    time_input = page.locator(".scheduled-picker input").first
    want = f"{when.hour:02d}:{when.minute:02d}"
    # hours in the left column, minutes (5-minute steps) in the right
    for col, value, settled in (
            (".tiktok-timepicker-left", f"{when.hour:02d}", f"{when.hour:02d}:"),
            (".tiktok-timepicker-right", f"{when.minute:02d}", want)):
        if page.locator(".tiktok-timepicker-invisible").count():
            time_input.click()
            page.wait_for_timeout(400)
        if not _pick(page, col, value):
            raise RuntimeError(f"TikTok time picker has no {col} option {value!r}")
        if not _settle(page, 0, settled):
            raise RuntimeError(
                f"TikTok time picker settled on {time_input.input_value()!r}, "
                f"wanted {want}")
    page.keyboard.press("Escape")


def _set_date(page, when: datetime) -> None:
    date_input = page.locator(".scheduled-picker input").nth(1)
    want = when.strftime("%Y-%m-%d")
    cal = page.locator(".calendar-wrapper")

    # The grid renders adjacent-month days too, so a day number can appear twice
    # (e.g. Aug 1 as a trailing cell in the July view). Read the input back after
    # each pick and try the next candidate if we landed in the wrong month.
    for candidate in range(2):
        if date_input.input_value() == want:
            return
        date_input.click()
        page.wait_for_timeout(400)
        # walk forward to the target month; the 10-day window never goes backwards
        for _ in range(3):
            shown = (cal.locator(".month-title").inner_text().strip(),
                     cal.locator(".year-title").inner_text().strip())
            if shown == (when.strftime("%B"), str(when.year)):
                break
            cal.locator(".arrow").nth(1).click()
            page.wait_for_timeout(400)
        if not _pick(page, "span.day.valid", str(when.day), candidate):
            break
        _settle(page, 1, want, timeout_ms=3_000)

    if date_input.input_value() != want:
        raise RuntimeError(
            f"TikTok date picker shows {date_input.input_value()!r}, wanted {want} "
            "— slot may be outside the 10-day scheduling window")


def _wait_for_upload(page) -> None:
    """Block until the video finishes uploading and the post button is live."""
    page.wait_for_selector('button[data-e2e="post_video_button"]',
                           timeout=UPLOAD_TIMEOUT_MS)
    post = page.locator('button[data-e2e="post_video_button"]')
    page.wait_for_function(
        """() => {
            const b = document.querySelector('button[data-e2e="post_video_button"]');
            if (!b || b.disabled) return false;
            return !/uploading|\\d+%/i.test(document.body.innerText);
        }""",
        timeout=UPLOAD_TIMEOUT_MS,
    )
    return post


def _confirm_posted(page, timeout_s: int = 300) -> None:
    """Wait for TikTok to acknowledge the post, dismissing any confirm dialog.

    Success looks different depending on the account state — a 'Manage posts'
    modal, a redirect to the content list, or the picker resetting to empty — so
    accept any of them, and dump what was on screen if none arrives."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        if "/upload" not in page.url:
            return
        if re.search(r"Select video to upload|Manage posts|Your video (has been|is) "
                     r"(scheduled|posted)|has been scheduled", body, re.I):
            return
        # some accounts get an extra confirmation step; "Schedule" is deliberately
        # not in this list — it is also the label of the main post button
        for label in ("Post now", "Confirm", "Got it", "OK"):
            if _dismiss_modal(page, label):
                break
    raise TimeoutError(
        "TikTok never confirmed the post. Last screen text: "
        + page.inner_text("body")[:400].replace("\n", " | ")
    )


def post(video_file: Path, caption: str, publish_at: datetime | None, cfg: dict,
         commit: bool = True) -> str:
    """Upload `video_file` to TikTok, scheduled for `publish_at` (local tz) or
    posted immediately when it is None. Returns a short status token stored as
    tiktok_id — Web Studio never exposes a publish id to the page.

    commit=False fills the whole form then discards instead of posting: a smoke
    test for the selectors after TikTok ships a UI change."""
    from playwright.sync_api import sync_playwright

    video_file = Path(video_file)
    if not video_file.exists():
        raise FileNotFoundError(video_file)

    if publish_at is not None:
        when = publish_at.astimezone()  # TikTok's picker works in local wall time
        lead = when - datetime.now(when.tzinfo)
        if lead < MIN_LEAD:
            log.warning("Publish slot is only %s out — posting to TikTok now", lead)
            publish_at = None
        elif lead > MAX_LEAD:
            raise ValueError(
                f"publish slot {when:%Y-%m-%d %H:%M} is {lead.days}d out; TikTok "
                "only schedules 10 days ahead")

    with sync_playwright() as pw:
        ctx = _launch(pw, cfg)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            # the real input is hidden behind the styled "Select video" button
            page.wait_for_selector('input[type="file"]', state="attached", timeout=60_000)
            if "login" in page.url:
                raise RuntimeError(
                    "TikTok profile is signed out — re-login in the browser profile")
            # an interrupted run leaves "A video you were editing wasn't saved.
            # Continue editing?" in front of the picker
            page.wait_for_timeout(2000)
            _dismiss_modal(page, "Discard")

            log.info("Uploading %s (%.1f MB) to TikTok Studio",
                     video_file.name, video_file.stat().st_size / 1e6)
            page.locator('input[type="file"]').set_input_files(str(video_file))
            post_btn = _wait_for_upload(page)
            # the draft prompt can also land *after* the upload finishes, where it
            # would block every click on the form underneath
            _dismiss_modal(page, "Discard")

            _set_caption(page, caption)

            if publish_at is not None:
                when = publish_at.astimezone()
                _open_schedule(page)
                # date first: while the date still reads today, the time picker
                # clamps away any hour already in the past
                _set_date(page, when)
                _set_time(page, when)
                log.info("Scheduled TikTok post for %s", when.isoformat())

            if not commit:
                log.info("commit=False — discarding instead of posting")
                page.locator('button[data-e2e="discard_post_button"]').click()
                page.wait_for_timeout(800)
                _dismiss_modal(page, "Discard")
                page.wait_for_timeout(1500)
                return "smoke_test_ok"

            post_btn.click()
            _confirm_posted(page)
        finally:
            ctx.close()
            # Edge can outlive close(); clear the lock so the next run can launch
            _kill_stale(_profile_dir(cfg))

    return f"scheduled:{publish_at.isoformat()}" if publish_at else "posted_now"
