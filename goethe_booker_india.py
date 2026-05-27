#!/usr/bin/env python
"""
Goethe India booking watcher + auto-book flow.

Updated for: https://www.goethe.de/ins/in/de/spr/prf.html
Default exam: B2 India — change START_URL below to any other exam.

Available India exam URLs:
  A1 Fit in Deutsch 1 : https://www.goethe.de/ins/in/de/spr/prf/gzfit1.cfm
  A1 Start Deutsch 1  : https://www.goethe.de/ins/in/de/spr/prf/gzsd1.cfm
  A2 Fit in Deutsch   : https://www.goethe.de/ins/in/de/spr/prf/gzfit2.cfm
  A2                  : https://www.goethe.de/ins/in/de/spr/prf/gzsd2.cfm
  B1                  : https://www.goethe.de/ins/in/de/spr/prf/gzb1.cfm
  B2                  : https://www.goethe.de/ins/in/de/spr/prf/gzb2.cfm  <-- DEFAULT
  C1                  : https://www.goethe.de/ins/in/de/spr/prf/gzc1.cfm
  C2                  : https://www.goethe.de/ins/in/de/spr/prf/gzc2.cfm

India contact info (for reference):
  Mumbai  : +91 91 6740 3569 | Exams-mumbai@goethe.de
  Delhi   : +91 11 2347 1100 | Language-delhi@goethe.de
  Bangalore: +91 80 2251 1300 | German-exams-bangalore@goethe.de
  Chennai : +91 44 2833 1644 | german.chennai@goethe.de
  Pune    : +91 20 66447120  | Exams-pune@goethe.de
  Kolkata : +91 33 2264 6398 | German-Kolkata@goethe.de
"""

import asyncio
import contextlib
import csv
import os
import pathlib
import platform
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# ---------------------------------------------------------------------------
# Optional dotenv support
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False


# ---------------------------------------------------------------------------
# Embedded fallback credentials / data  *** EDIT THESE ***
# ---------------------------------------------------------------------------
EMBED_EMAIL = "dummy@example.com"
EMBED_PASSWORD = "DummyPass123!"
EMBED_PHONE = "+919000000000"       # Indian phone format
EMBED_FIRST_NAME = "Test"
EMBED_SURNAME = "User"
EMBED_CITY = "Mumbai"               # Indian city (was COUNTY for Kenya)
EMBED_DOB = "2000-01-01"            # YYYY-MM-DD
EMBED_PLACE_OF_BIRTH = "Mumbai"
EMBED_ZIP = "400001"                # Indian PIN code
EMBED_STATE = "Maharashtra"         # Indian state (extra field)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Alarm (winsound on Windows, bell fallback elsewhere)
# ---------------------------------------------------------------------------
if platform.system().lower().startswith("win"):
    try:
        import winsound  # type: ignore

        def _alarm_beep(freq: int, duration_ms: int) -> None:
            try:
                winsound.Beep(freq, duration_ms)
            except Exception:
                sys.stderr.write("[alarm] winsound.Beep failed\n")
    except Exception:
        def _alarm_beep(freq: int, duration_ms: int) -> None:
            sys.stdout.write("\a")
            sys.stdout.flush()
            time.sleep(duration_ms / 1000)
else:
    def _alarm_beep(freq: int, duration_ms: int) -> None:
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(duration_ms / 1000)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Change this URL to whichever exam level you want to book
START_URL = "https://www.goethe.de/ins/in/de/spr/prf/gzb2.cfm"

# Set your preferred city/location from the India site.
# Choices: Bangalore, Chennai, Kolkata, Mumbai, New Delhi, Pune
# (used for logging / future location filtering)
PREFERRED_LOCATION = "Mumbai"

MAX_REFRESH_MS = 800
STEP_WAIT_MS = 200
DEFAULT_TIMEOUT = 5_000
ALARM_BEEP_FREQ = 950
ALARM_BEEP_DURATION_MS = 2_000
ALARM_REPEAT_SEC = 60

# Step trigger texts (case-insensitive; German + English variants)
TXT_SELECT_MODULES = "select modules"
TXT_BUCHEN = "buchen"               # German "Book"
TXT_PRUFUNG_BUCHEN = "prüfung buchen"
TXT_CONTINUE = "continue"
TXT_WEITER = "weiter"               # German "Continue"
TXT_BOOK_FOR_MYSELF = "book for myself"
TXT_FUR_MICH = "für mich buchen"    # German "Book for myself"
TXT_LOGIN = "log in"
TXT_ANMELDEN = "anmelden"           # German "Log in"
TXT_ORDER_SUBJECT_TO_CHANGE = "order, subject to change"
TXT_KOSTENPFLICHTIG = "kostenpflichtig bestellen"  # German order button
TXT_CONFIRMATION = "You will receive email confirmation of your booking."
TXT_CONFIRMATION_DE = "Sie erhalten eine E-Mail-Bestätigung Ihrer Buchung."
# Privacy banner
TXT_PRIVACY_ACCEPT = "accept all"
TXT_PRIVACY_ACCEPT_DE = "alle akzeptieren"
TXT_PRIVACY_DENY = "deny"
TXT_PRIVACY_ABLEHNEN = "ablehnen"
TXT_PRIVACY_SETTINGS = "settings"
TXT_PRIVACY_EINSTELLUNGEN = "einstellungen"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_F = None

def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def _log(level: str, msg: str, email: str = ""):
    line = f"{_ts()} [{level}] {email} {msg}".rstrip() + "\n"
    sys.stdout.write(line)
    if _LOG_F:
        try:
            _LOG_F.write(line)
            _LOG_F.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Data model — updated for India (state replaces county)
# ---------------------------------------------------------------------------
@dataclass
class Student:
    email: str
    password: str
    phone: str
    first_name: str
    surname: str
    city: str           # city in India (was county for Kenya)
    state: str          # Indian state
    dob: str
    place_of_birth: str
    zip_code: str       # 6-digit PIN code in India

    @classmethod
    def from_row(cls, row: Dict[str, str]) -> "Student":
        return cls(
            email=row.get("email", ""),
            password=row.get("password", ""),
            phone=row.get("phone", ""),
            first_name=row.get("first_name", ""),
            surname=row.get("surname", ""),
            city=row.get("city", row.get("county", "")),
            state=row.get("state", ""),
            dob=row.get("dob", ""),
            place_of_birth=row.get("place_of_birth", ""),
            zip_code=row.get("zip_code", ""),
        )

DUMMY_STUDENT = Student(
    email=EMBED_EMAIL,
    password=EMBED_PASSWORD,
    phone=EMBED_PHONE,
    first_name=EMBED_FIRST_NAME,
    surname=EMBED_SURNAME,
    city=EMBED_CITY,
    state=EMBED_STATE,
    dob=EMBED_DOB,
    place_of_birth=EMBED_PLACE_OF_BIRTH,
    zip_code=EMBED_ZIP,
)


# ---------------------------------------------------------------------------
# Small async helpers
# ---------------------------------------------------------------------------
async def short_wait(ms: int = STEP_WAIT_MS):
    await asyncio.sleep(ms / 1000)

def _rand_refresh_delay() -> float:
    return random.uniform(0, MAX_REFRESH_MS) / 1000

async def _safe_click(el):
    with contextlib.suppress(Exception):
        await el.scroll_into_view_if_needed()
    await el.click()

async def _find_by_text(page: Page, text: str):
    pattern = re.compile(re.escape(text.strip()), re.I)
    try:
        return page.get_by_role("button", name=pattern)
    except Exception:
        return page.get_by_text(pattern)

async def _first_visible(page: Page, candidates: List[Any]):
    for c in candidates:
        if c is None:
            continue
        try:
            await c.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            return c
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return None

async def _try_click_text(page: Page, *texts: str) -> bool:
    cands = [await _find_by_text(page, t) for t in texts if t]
    el = await _first_visible(page, cands)
    if el is None:
        return False
    await _safe_click(el)
    await short_wait()
    return True


# ---------------------------------------------------------------------------
# Privacy banner — now handles German variants too
# ---------------------------------------------------------------------------
async def _handle_privacy_popup(page: Page, email: str):
    _log("INFO", "checking privacy popup", email)
    try:
        if await _try_click_text(
            page,
            TXT_PRIVACY_ACCEPT,
            TXT_PRIVACY_ACCEPT_DE,
            "accept",
            "accept all",
            "alle akzeptieren",
        ):
            _log("PASS", "privacy accepted", email)
            await short_wait()
            return
        if await _try_click_text(
            page, TXT_PRIVACY_DENY, TXT_PRIVACY_ABLEHNEN, "deny"
        ):
            _log("PASS", "privacy denied fallback", email)
            await short_wait()
            return
        if await _try_click_text(
            page, TXT_PRIVACY_SETTINGS, TXT_PRIVACY_EINSTELLUNGEN, "settings"
        ):
            _log("INFO", "privacy settings clicked", email)
            await short_wait()
            return
    except Exception as e:
        _log("ERROR", f"privacy popup handling error {e}", email)


# ---------------------------------------------------------------------------
# Start / restart
# ---------------------------------------------------------------------------
async def _goto_start(page: Page, email: str):
    _log("INFO", f"navigating start {START_URL}", email)
    await page.goto(START_URL, wait_until="domcontentloaded")
    await short_wait()
    await _handle_privacy_popup(page, email)


# ---------------------------------------------------------------------------
# Booking open detection
# ---------------------------------------------------------------------------
async def _find_enabled_exam_button(page: Page) -> Optional[Any]:
    buttons = await page.query_selector_all(".pr-buttons button")
    for b in buttons:
        try:
            if await b.get_attribute("disabled") is None:
                return b
        except Exception:
            continue
    return None

async def _poll_until_select_modules(page: Page, email: str):
    """Reload loop until booking becomes possible (German + English triggers)."""
    _log("INFO", "polling for booking button", email)
    while True:
        # 1. English "SELECT MODULES"
        for trigger in [TXT_SELECT_MODULES, TXT_BUCHEN, TXT_PRUFUNG_BUCHEN]:
            try:
                el = await _find_by_text(page, trigger)
                await el.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
                await _safe_click(el)
                await short_wait()
                _log("PASS", f"booking button clicked: {trigger}", email)
                return
            except PlaywrightTimeoutError:
                pass
            except Exception as e:
                _log("ERROR", f"booking trigger '{trigger}' error {e}", email)

        # 2. Enabled exam button fallback
        try:
            exam_btn = await _find_enabled_exam_button(page)
            if exam_btn:
                _log("INFO", "enabled exam button found fallback", email)
                await _safe_click(exam_btn)
                await short_wait()
                _log("PASS", "exam button clicked (fallback)", email)
                return
        except Exception as e:
            _log("ERROR", f"exam button scan error {e}", email)

        # 3. Reload
        delay = _rand_refresh_delay()
        _log("INFO", f"booking not visible; reloading after {delay:.3f}s", email)
        await asyncio.sleep(delay)
        try:
            await page.reload(wait_until="domcontentloaded")
            _log("RELOAD_OK", "page reloaded", email)
        except Exception as e:
            _log("RELOAD_ERR", f"reload error {e}", email)
            continue
        await short_wait()
        await _handle_privacy_popup(page, email)


# ---------------------------------------------------------------------------
# Step advance wrapper
# ---------------------------------------------------------------------------
async def _advance_or_restart(page: Page, texts: List[str], email: str) -> bool:
    _log("INFO", f"advancing via {texts}", email)
    ok = await _try_click_text(page, *texts)
    if ok:
        _log("PASS", f"clicked {texts}", email)
        return True
    _log("FAIL", f"did not find {texts}, restart", email)
    await _goto_start(page, email)
    return False


# ---------------------------------------------------------------------------
# Login — handles German "Anmelden" label too
# ---------------------------------------------------------------------------
async def _login(page: Page, student: Student) -> bool:
    email = student.email
    _log("INFO", "login step", email)
    selectors = ["input[type=email]", "#email", "input[name=email]", "input[name=username]"]
    pw_selectors = ["input[type=password]", "#password", "input[name=password]"]
    filled = False

    for sel in selectors:
        try:
            f = await page.query_selector(sel)
            if f:
                await f.fill(student.email)
                filled = True
                _log("INFO", f"filled email {sel}", email)
                break
        except Exception as e:
            _log("ERROR", f"email fill {sel} {e}", email)

    for sel in pw_selectors:
        try:
            f = await page.query_selector(sel)
            if f:
                await f.fill(student.password)
                _log("INFO", f"filled password {sel}", email)
                break
        except Exception as e:
            _log("ERROR", f"password fill {sel} {e}", email)

    if filled and await _try_click_text(
        page, TXT_LOGIN, TXT_ANMELDEN, "login", "sign in", "log-in", "einloggen"
    ):
        _log("PASS", "login submit clicked", email)
        await short_wait()
        return True

    _log("FAIL", "login step failed restart", email)
    await _goto_start(page, email)
    return False


# ---------------------------------------------------------------------------
# Personal details — updated field mapping for India
# ---------------------------------------------------------------------------
async def _fill_personal_form(page: Page, student: Student) -> bool:
    email = student.email
    _log("INFO", "personal form step", email)

    # India-specific field mapping: added state, updated city (was county)
    mapping = [
        ("phone", student.phone),
        ("mobile", student.phone),
        ("handy", student.phone),          # German field name
        ("first", student.first_name),
        ("given", student.first_name),
        ("vorname", student.first_name),   # German "first name"
        ("sur", student.surname),
        ("last", student.surname),
        ("nachname", student.surname),     # German "surname"
        ("familienname", student.surname),
        ("city", student.city),
        ("stadt", student.city),           # German "city"
        ("ort", student.city),
        ("state", student.state),
        ("bundesland", student.state),
        ("birth", student.place_of_birth),
        ("geburtsort", student.place_of_birth),  # German "place of birth"
        ("zip", student.zip_code),
        ("pin", student.zip_code),
        ("post", student.zip_code),
        ("plz", student.zip_code),         # German "postal code"
        ("date", student.dob),
        ("dob", student.dob),
        ("geburtsdatum", student.dob),     # German "date of birth"
    ]
    lower_map = [(k.lower(), v) for k, v in mapping if v]

    inputs = await page.query_selector_all("input,select,textarea")
    for inp in inputs:
        try:
            name = (await inp.get_attribute("name") or "").lower()
            placeholder = (await inp.get_attribute("placeholder") or "").lower()
            aria = (await inp.get_attribute("aria-label") or "").lower()
            match_txt = f"{name} {placeholder} {aria}"
            for key, val in lower_map:
                if key in match_txt:
                    tag = await inp.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        await inp.select_option(label=val)
                    else:
                        await inp.fill(val)
                    _log("INFO", f"filled field '{key}'", email)
                    break
        except Exception as e:
            _log("ERROR", f"field fill error {e}", email)

    # Continue button — try German and English
    if await _try_click_text(page, TXT_CONTINUE, TXT_WEITER, "weiter", "continue"):
        _log("PASS", "personal form continue", email)
        return True

    _log("FAIL", "personal form continue missing restart", email)
    await _goto_start(page, email)
    return False


# ---------------------------------------------------------------------------
# Alarm controller
# ---------------------------------------------------------------------------
class AlarmController:
    def __init__(self):
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def stop(self):
        self._stop.set()

    async def _beep_once(self):
        await asyncio.to_thread(_alarm_beep, ALARM_BEEP_FREQ, ALARM_BEEP_DURATION_MS)

    async def _run(self):
        while not self._stop.is_set():
            await self._beep_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=ALARM_REPEAT_SEC)
            except asyncio.TimeoutError:
                continue

    def start(self):
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def wait_stopped(self):
        if self._task:
            await self._task


async def _install_doubleclick_stop(page: Page, alarm: AlarmController, email: str):
    async def _stop_alarm_binding(_source, *_args):
        _log("INFO", "double-click stop alarm", email)
        alarm.stop()

    await page.expose_binding("__goetheStopAlarm", _stop_alarm_binding)
    await page.add_init_script("""
        (() => {
          document.addEventListener('dblclick', () => {
            if (window.__goetheStopAlarm) {
              window.__goetheStopAlarm();
            }
          }, { once: true });
        })();
    """)


# ---------------------------------------------------------------------------
# Main booking flow
# ---------------------------------------------------------------------------
async def run_booking(page: Page, student: Student) -> bool:
    email = student.email

    await _goto_start(page, email)
    await _poll_until_select_modules(page, email)

    # Step 2: Continue / Weiter
    if not await _advance_or_restart(page, [TXT_CONTINUE, TXT_WEITER], email):
        return False

    # Step 3: Book for myself / Für mich buchen
    if not await _advance_or_restart(
        page, [TXT_BOOK_FOR_MYSELF, TXT_FUR_MICH, "für mich"], email
    ):
        return False

    # Step 4: Login
    if not await _login(page, student):
        return False

    # Step 5: Personal details
    if not await _fill_personal_form(page, student):
        return False

    # Step 6: Continue / Weiter (summary page)
    if not await _advance_or_restart(page, [TXT_CONTINUE, TXT_WEITER], email):
        return False

    # Step 7: Order button (English + German)
    if not await _advance_or_restart(
        page,
        [TXT_ORDER_SUBJECT_TO_CHANGE, TXT_KOSTENPFLICHTIG, "kostenpflichtig"],
        email,
    ):
        return False

    # Step 8: Confirmation
    try:
        for conf_text in [TXT_CONFIRMATION, TXT_CONFIRMATION_DE]:
            try:
                conf = await _find_by_text(page, conf_text)
                await conf.wait_for(state="visible", timeout=30_000)
                _log("PASS", f"confirmation text visible: {conf_text}", email)
                break
            except PlaywrightTimeoutError:
                continue
        else:
            raise Exception("no confirmation text found")
    except Exception as e:
        _log("FAIL", f"confirmation not visible {e}", email)
        await _goto_start(page, email)
        return False

    # Alarm until double-click
    alarm = AlarmController()
    await _install_doubleclick_stop(page, alarm, email)
    alarm.start()
    _log("INFO", "alarm started — double-click page to stop", email)
    await alarm.wait_stopped()
    _log("PASS", "alarm stopped — booking complete", email)
    return True


# ---------------------------------------------------------------------------
# Browser orchestration
# ---------------------------------------------------------------------------
async def _new_context(browser: Browser) -> BrowserContext:
    return await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="de-IN",          # German locale for India site
        timezone_id="Asia/Kolkata",
    )

async def _run_for_student(browser: Browser, headed: bool, student: Student) -> bool:
    email = student.email
    _log("INFO", "new context", email)
    ctx = await _new_context(browser)
    page = await ctx.new_page()
    ok = False
    try:
        ok = await run_booking(page, student)
    finally:
        await ctx.close()
        _log("INFO", "context closed", email)
    return ok

async def main_async(headless: bool, students: List[Student]):
    _log("INFO", f"launching browser headless={headless} | target={START_URL}", "")
    _log("INFO", f"preferred location: {PREFERRED_LOCATION}", "")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        results = []
        for s in students:
            _log("INFO", "begin student", s.email)
            ok = await _run_for_student(browser, not headless, s)
            results.append((s.email, ok))
            _log("INFO", f"end student success={ok}", s.email)
        await browser.close()
    _log("INFO", "browser closed", "")
    return results


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_students_from_csv(path: Optional[str]) -> List[Student]:
    if not path:
        return [DUMMY_STUDENT]
    students: List[Student] = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(Student.from_row(row))
    except Exception as e:
        _log("ERROR", f"csv read error {e}", "")
    return students or [DUMMY_STUDENT]

def load_env(env_path: Optional[str]):
    if env_path and os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()

def student_from_env() -> Student:
    return Student(
        email=os.getenv("GOETHE_EMAIL", EMBED_EMAIL),
        password=os.getenv("GOETHE_PASSWORD", EMBED_PASSWORD),
        phone=os.getenv("GOETHE_PHONE", EMBED_PHONE),
        first_name=os.getenv("GOETHE_FIRST_NAME", EMBED_FIRST_NAME),
        surname=os.getenv("GOETHE_SURNAME", EMBED_SURNAME),
        city=os.getenv("GOETHE_CITY", EMBED_CITY),
        state=os.getenv("GOETHE_STATE", EMBED_STATE),
        dob=os.getenv("GOETHE_DOB", EMBED_DOB),
        place_of_birth=os.getenv("GOETHE_PLACE_OF_BIRTH", EMBED_PLACE_OF_BIRTH),
        zip_code=os.getenv("GOETHE_ZIP", EMBED_ZIP),
    )

def build_students(ignore_env: bool, env_only: bool, csv_path: Optional[str]) -> List[Student]:
    if ignore_env:
        return [DUMMY_STUDENT] if env_only else load_students_from_csv(csv_path)
    return [student_from_env()] if env_only else load_students_from_csv(csv_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: List[str]):
    import argparse
    p = argparse.ArgumentParser(
        prog="goethe-booker-india",
        description="Goethe exam booking bot for India (goethe.de/ins/in)",
    )
    p.add_argument("--csv", "--students", dest="csv", help="CSV file with student rows")
    p.add_argument("--env", help=".env path")
    p.add_argument("--env-only", action="store_true",
                   help="Use env (or embedded) single student, ignore CSV")
    p.add_argument("--ignore-env", action="store_true",
                   help="Do not read env vars; use embedded values")
    p.add_argument("--headless", action="store_true", help="Run browser headless")
    p.add_argument("--headed", action="store_true", help="Force headed mode")
    p.add_argument("--log", help="log file path", default="goethe_booking_india.log")
    p.add_argument(
        "--exam",
        choices=["a1fit","a1start","a2fit","a2","b1","b2","c1","c2"],
        default=None,
        help="Override exam level (changes START_URL)",
    )
    args = p.parse_args(argv)
    headed = not args.headless
    if args.headed:
        headed = True
    return args, headed

EXAM_URLS = {
    "a1fit":  "https://www.goethe.de/ins/in/de/spr/prf/gzfit1.cfm",
    "a1start":"https://www.goethe.de/ins/in/de/spr/prf/gzsd1.cfm",
    "a2fit":  "https://www.goethe.de/ins/in/de/spr/prf/gzfit2.cfm",
    "a2":     "https://www.goethe.de/ins/in/de/spr/prf/gzsd2.cfm",
    "b1":     "https://www.goethe.de/ins/in/de/spr/prf/gzb1.cfm",
    "b2":     "https://www.goethe.de/ins/in/de/spr/prf/gzb2.cfm",
    "c1":     "https://www.goethe.de/ins/in/de/spr/prf/gzc1.cfm",
    "c2":     "https://www.goethe.de/ins/in/de/spr/prf/gzc2.cfm",
}

def _init_log(path: str):
    global _LOG_F
    try:
        p = pathlib.Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        _LOG_F = open(p, "w", encoding="utf-8")
    except Exception:
        _LOG_F = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None):
    global START_URL
    if argv is None:
        argv = sys.argv[1:]
    args, headed = _parse_args(argv)
    if args.exam:
        START_URL = EXAM_URLS[args.exam]
        _log("INFO", f"exam override -> {START_URL}", "")
    _init_log(args.log)
    load_env(args.env)
    students = build_students(args.ignore_env, args.env_only, args.csv)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(main_async(headless=not headed, students=students))
    for email, ok in results:
        print(f"{email}: {'SUCCESS' if ok else 'FAILED'}")
    if _LOG_F:
        _LOG_F.close()

if __name__ == "__main__":
    main()
