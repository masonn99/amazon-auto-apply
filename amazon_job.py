import asyncio
import json
import re
import time
import traceback
from argparse import ArgumentParser
from datetime import date, datetime, timedelta
from os import makedirs
from os.path import abspath, dirname, exists, join

import discord
import requests
import undetected_chromedriver as uc
from decouple import config
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


AMAZON_EM = config('AMAZON_EM')
AMAZON_PW = config('AMAZON_PW')
DISC_KEY = config('DISC_KEY')
DISC_CHANNEL = int(config('DISC_CHANNEL'))
OTP_CODE = "+"

LOGIN_URL = "https://passport.amazon.jobs/"
SEARCH_URL = "https://www.amazon.jobs/en/search.json"
SEARCH_COUNTRY = "USA"
SEARCH_QUERIES = ["Software Development Engineer II", "SDE2", "SDE II"]
# Anchored to this file's own location (not the shell's cwd) so the Chrome
# profile — and thus the logged-in session — is found consistently no matter
# where this script gets launched from. A cwd-relative path here would silently
# start a fresh, empty profile on any run from a different directory, forcing
# a full OTP login again.
AMAZON_DIR = join(dirname(abspath(__file__)), "amazon")
SEEN_JOBS_PATH = join(AMAZON_DIR, "seen_jobs.json")
CHROME_PROFILE_DIR = join(AMAZON_DIR, "chrome_profile")
CAPTURE_DIR = join(AMAZON_DIR, "capture")
JOB_QA_PATH = join(AMAZON_DIR, "job_qa.json")

# Fixed answers for the two Work Eligibility fields that aren't already carried
# over from the profile (everything else on that panel defaults correctly).
WORK_ELIGIBILITY_ANSWERS = {
    "REQUIRE_SPONSORSHIP": "NO",
    "GEF_EXT_USA_GOVERNMENT_EMPLOYEE": "NEVER",
}

# For "how many years of experience with X" style dropdowns, pick whichever
# option's range contains this many years (falling back to the highest bucket
# at or below it) instead of asking on Discord every time a new language shows up.
TARGET_EXPERIENCE_YEARS = 3

# "Software Development Engineer II" / "SDE2" / "SDE II", not I/III/Senior/etc.
SDE2_TITLE_RE = re.compile(r"(?i)\b(sde\s*2|sde\s*ii|software development engineer\s*(ii|2))\b")
EXCLUDE_TITLE_RE = re.compile(r"(?i)\b(senior|sr\.?|principal|iii|manager|intern|lead)\b")


def main():
    cutoff = date.today() - timedelta(days=options['posted_within_days'])
    seen_ids = load_seen_ids()
    jobs = search_sde2_jobs(min_posted_date=cutoff)
    new_jobs = [job for job in jobs if job["id_icims"] not in seen_ids]

    if not new_jobs:
        print(f"No new SDE2 jobs found in the US posted on/after {cutoff}.")
        return

    print(f"Found {len(new_jobs)} new SDE2 job(s) posted on/after {cutoff}:")
    for job in new_jobs:
        print(f" - {job['title']} ({job['normalized_location']}) "
              f"https://www.amazon.jobs{job['job_path']}")

    if options['dry_run']:
        return

    makedirs(AMAZON_DIR, exist_ok=True)
    driver = browser_options(True)
    try:
        login(driver)

        for job in new_jobs:
            submit_job_application(driver, job)
            seen_ids.add(job["id_icims"])
            save_seen_ids(seen_ids)
    except Exception:
        driver.save_screenshot(join(AMAZON_DIR, "debug_crash.png"))
        print(f"[debug] crashed at url={driver.current_url} title={driver.title!r}")
        traceback.print_exc()
        input("Something went wrong. Browser left open for you to inspect it — "
              "press Enter here once you're done to close it...")
    finally:
        driver.quit()


def search_sde2_jobs(min_posted_date=None):
    jobs_by_id = {}
    for query in SEARCH_QUERIES:
        offset = 0
        while True:
            response = requests.get(SEARCH_URL, params={
                "base_query": query,
                "country": SEARCH_COUNTRY,
                "result_limit": 100,
                "offset": offset,
            }, timeout=15)
            response.raise_for_status()
            data = response.json()
            page_jobs = data.get("jobs", [])
            if not page_jobs:
                break
            for job in page_jobs:
                jobs_by_id[job["id_icims"]] = job
            offset += len(page_jobs)
            if offset >= data.get("hits", 0):
                break

    matched = [
        job for job in jobs_by_id.values()
        if SDE2_TITLE_RE.search(job.get("title", ""))
        and not EXCLUDE_TITLE_RE.search(job.get("title", ""))
    ]

    if min_posted_date is not None:
        matched = [
            job for job in matched
            if (posted := parse_posted_date(job.get("posted_date"))) is not None
            and posted >= min_posted_date
        ]

    return matched


def parse_posted_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(" ".join(value.split()), "%B %d, %Y").date()
    except ValueError:
        return None


def load_seen_ids():
    if not exists(SEEN_JOBS_PATH):
        return set()
    with open(SEEN_JOBS_PATH) as f:
        return set(json.load(f))


def save_seen_ids(seen_ids):
    makedirs(AMAZON_DIR, exist_ok=True)
    with open(SEEN_JOBS_PATH, "w") as f:
        json.dump(sorted(seen_ids), f, indent=2)


def debug_dump(driver, label):
    makedirs(AMAZON_DIR, exist_ok=True)
    screenshot_path = join(AMAZON_DIR, f"debug_{label}.png")
    driver.save_screenshot(screenshot_path)
    print(f"[debug] {label}: url={driver.current_url} title={driver.title!r}")
    print("[debug] inputs:", driver.execute_script(
        "return Array.from(document.querySelectorAll('input')).map(el => "
        "({id: el.id, name: el.name, type: el.type, placeholder: el.placeholder, "
        "value: el.type === 'file' ? undefined : el.value, checked: el.checked}));"
    ))
    print("[debug] selects:", driver.execute_script(
        "return Array.from(document.querySelectorAll('select')).map(el => "
        "({id: el.id, name: el.name, value: el.value, "
        "options: Array.from(el.options).map(o => o.text)}));"
    ))
    print("[debug] textareas:", driver.execute_script(
        "return Array.from(document.querySelectorAll('textarea')).map(el => "
        "({id: el.id, name: el.name, placeholder: el.placeholder}));"
    ))
    print("[debug] buttons:", driver.execute_script(
        "return Array.from(document.querySelectorAll('button')).map(el => "
        "({id: el.id, type: el.type, text: el.innerText.trim()}));"
    ))
    return screenshot_path


def set_input_value(driver, element, value):
    # Bypasses send_keys entirely — Chrome's inline autofill suggestion can
    # interfere with simulated keystrokes and duplicate the typed value.
    # This is a React app: it tracks input state via its own instrumentation,
    # so a plain `.value =` assignment gets silently reverted on next render.
    # Calling the native prototype setter directly (instead of any instance-level
    # setter React installed) is the standard workaround for React-controlled inputs.
    driver.execute_script(
        "const proto = Object.getPrototypeOf(arguments[0]);"
        "const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;"
        "nativeSetter.call(arguments[0], arguments[1]);"
        "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
        "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
        element, value
    )


def login(driver, target_url=None):
    # Navigating straight to the target job/application URL (instead of the bare
    # LOGIN_URL) lets Amazon's own auth redirect remember where to send us back
    # after login — otherwise we land on the generic candidate home page.
    driver.get(target_url or LOGIN_URL)
    wait_until(driver, page_loaded=True)

    if "passport.amazon.jobs" not in driver.current_url:
        # Persistent Chrome profile already had a valid session — no login needed.
        print(f"[login] already authenticated, url={driver.current_url}")
        return

    debug_dump(driver, "login_page")

    email_field = driver.find_element(By.ID, "loginFormUsernameInputField")
    set_input_value(driver, email_field, AMAZON_EM)
    password_field = driver.find_element(By.ID, "loginFormPasswordInputField")
    set_input_value(driver, password_field, AMAZON_PW)
    wait_until(
        driver=driver,
        js=(f'document.querySelector("#loginFormUsernameInputField").value == "{AMAZON_EM}" && '
            f'document.querySelector("#loginFormPasswordInputField").value == "{AMAZON_PW}"')
    )

    # A bare button[type="submit"] selector grabs whichever matching button is
    # first in the DOM — the page also has unrelated submit buttons (e.g. a
    # dismissible browser-compatibility banner), so match by visible text instead.
    driver.find_element(By.XPATH, '//button[normalize-space(.)="Log in to your account settings"]').click()
    # This portal swaps in the next screen via JS on the same page, so wait for
    # the verification-code screen's own text rather than document.readyState.
    wait_until(driver, text="Enter verification code")

    send_discord(
        "Amazon.jobs emailed you a verification code — check your inbox and reply "
        "here with it, prefixed with +, like +123456",
        wait_for_otp=True,
    )

    otp = OTP_CODE.replace("+", "").replace(" ", "")
    otp_field = driver.find_element(By.ID, "verificationFormCodeInputField")
    set_input_value(driver, otp_field, otp)
    wait_until(
        driver=driver,
        js=f'document.querySelector("#verificationFormCodeInputField").value == "{otp}"'
    )
    # Two buttons are type="submit" here ("Submit" and "Send me a new code"),
    # so match by text rather than a selector that could hit either.
    driver.find_element(By.XPATH, '//button[normalize-space(.)="Submit"]').click()
    wait_until(driver, url="not_verifyMfaOtp")

    debug_dump(driver, "post_login")

    if target_url and target_url not in driver.current_url:
        driver.get(target_url)
        wait_until(driver, page_loaded=True)


def load_job_qa():
    if not exists(JOB_QA_PATH):
        return {}
    with open(JOB_QA_PATH) as f:
        return json.load(f)


def save_job_qa(qa):
    makedirs(AMAZON_DIR, exist_ok=True)
    with open(JOB_QA_PATH, "w") as f:
        json.dump(qa, f, indent=2, sort_keys=True)


def normalize_question(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def get_active_panel(driver):
    return driver.execute_script(
        'const p = document.querySelector(\'div[role="tabpanel"].active\'); '
        'return p ? {id: p.id, label: p.getAttribute("aria-label")} : null;'
    )


def get_panel_select_questions(driver, panel_id):
    # Job-specific questions render as select2 dropdowns wrapping a hidden <select>;
    # data-questionid on the wrapper is the one stable hook since the select itself
    # has no id (select2 only generates one for its own rendered widget).
    # .question-label also contains a hidden .sr-only span that duplicates the
    # question text (plus " required") for screen readers — select the visible
    # <label> itself, not the wrapping div, or the text comes out doubled and
    # inconsistently suffixed depending on the question.
    return driver.execute_script(
        'const panel = document.getElementById(arguments[0]);'
        'if (!panel) return [];'
        'return Array.from(panel.querySelectorAll(".question[data-questionid]")).map(q => {'
        '  const labelEl = q.querySelector(".question-label label");'
        '  const text = labelEl ? labelEl.textContent.replace(/^Q\\.\\s*/, "").trim() : "";'
        '  const select = q.querySelector("select");'
        '  return select ? {'
        '    questionId: q.dataset.questionid,'
        '    text: text,'
        '    options: Array.from(select.options)'
        '      .filter(o => o.value !== "")'
        '      .map(o => ({value: o.value, text: o.textContent.trim()})),'
        '  } : null;'
        '}).filter(Boolean);',
        panel_id
    )


def set_select2_answer(driver, question_id, option_value):
    driver.execute_script(
        'const q = document.querySelector(\'.question[data-questionid="\' + arguments[0] + \'"]\');'
        'const select = q.querySelector("select");'
        'const $ = window.jQuery || window.$;'
        '$(select).val(arguments[1]).trigger("change");',
        question_id, option_value
    )


def ask_discord_question(prompt):
    # Generalized version of the OTP relay in login(): post a question to Discord
    # and block until the next reply comes in, then return its raw text.
    asyncio.set_event_loop(asyncio.new_event_loop())

    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Bot(intents=intents)
    answer = {}

    @bot.event
    async def on_ready():
        channel = bot.get_channel(DISC_CHANNEL)
        await channel.send(prompt)

    @bot.event
    async def on_message(message):
        if bot.user == message.author:
            return
        answer['text'] = message.content.strip()
        channel = bot.get_channel(DISC_CHANNEL)
        await channel.send(f"Got it: {answer['text']}")
        await bot.close()

    bot.run(DISC_KEY)
    return answer.get('text', '')


EXPERIENCE_RANGE_EPSILON = 1e-6


def _years(number, unit):
    # Amazon's dropdowns mix "months" and "years" within the same question
    # (e.g. "less than 10 months" next to "more than 3 years") — normalize
    # everything to years so buckets can be compared on one scale.
    n = float(number)
    return n / 12 if unit.startswith("month") else n


def parse_experience_range(option_text):
    # Returns (low, high) years implied by an option label, or None if it
    # doesn't look like an experience-range option at all. High is nudged
    # down by an epsilon for "less than X" phrasing so the boundary value X
    # itself correctly falls into the *next* bucket, matching Amazon's wording.
    text = option_text.strip().lower()

    m = re.match(r"^less than (\d+(?:\.\d+)?)\s*(year|month)s?$", text)
    if m:
        return (0.0, _years(m.group(1), m.group(2)) - EXPERIENCE_RANGE_EPSILON)

    m = re.match(r"^more than (\d+(?:\.\d+)?)\s*(year|month)s?$", text)
    if m:
        return (_years(m.group(1), m.group(2)), float('inf'))

    m = re.match(
        r"^(\d+(?:\.\d+)?)\s*(year|month)s?\s+to\s+less than\s+(\d+(?:\.\d+)?)\s*(year|month)s?$", text
    )
    if m:
        return (_years(m.group(1), m.group(2)), _years(m.group(3), m.group(4)) - EXPERIENCE_RANGE_EPSILON)

    m = re.match(r"^(\d+(?:\.\d+)?)\s*\+\s*(year|month)?s?$", text)
    if m:
        return (float(m.group(1)), float('inf'))

    m = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*(year|month)?s?$", text)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    m = re.match(r"^(\d+(?:\.\d+)?)\s*(year|month)s?$", text)
    if m:
        n = _years(m.group(1), m.group(2))
        return (n, n)

    if "no experience" in text or text in ("none", "0"):
        return (0.0, 0.0)

    return None


def pick_experience_option(options, target=TARGET_EXPERIENCE_YEARS):
    parsed = [(o, parse_experience_range(o['text'])) for o in options]
    parsed = [(o, r) for o, r in parsed if r is not None]
    if len(parsed) < len(options):
        return None  # not every option looked like a range — this isn't an experience question

    containing = [(o, r) for o, r in parsed if r[0] <= target <= r[1]]
    if containing:
        # Narrowest range wins; on a tie, prefer the bucket where target is
        # the *start* — "at least 3 years" reads better than "up to 3 years".
        containing.sort(key=lambda pair: (pair[1][1] - pair[1][0], -pair[1][0]))
        return containing[0][0]

    below = [(o, r) for o, r in parsed if r[0] <= target]
    if below:
        below.sort(key=lambda pair: -pair[1][0])
        return below[0][0]

    return min(parsed, key=lambda pair: pair[1][0])[0]


def pick_yes_no_experience_answer(question_text, options, target=TARGET_EXPERIENCE_YEARS):
    # Some job-specific questions ask the same thing as a range dropdown but
    # phrased as Yes/No, e.g. "Do you have 3+ years of relevant professional
    # experience?". Only fires when the question explicitly names a year
    # threshold AND mentions "experience" — guards against unrelated Yes/No
    # questions that happen to contain a number, like an age-eligibility check.
    option_texts = {o['text'].strip().lower() for o in options}
    if option_texts != {"yes", "no"}:
        return None
    if "experience" not in question_text.lower():
        return None
    m = re.search(r"(\d+)\+?\s*years?", question_text.lower())
    if not m:
        return None
    threshold = int(m.group(1))
    answer_text = "yes" if target >= threshold else "no"
    return next((o for o in options if o['text'].strip().lower() == answer_text), None)


def answer_job_specific_questions(driver, panel_id):
    qa = load_job_qa()
    for q in get_panel_select_questions(driver, panel_id):
        key = normalize_question(q['text'])
        auto_match = pick_experience_option(q['options']) or pick_yes_no_experience_answer(q['text'], q['options'])
        if key in qa:
            chosen_value = qa[key]
        elif auto_match is not None:
            chosen_value = auto_match['value']
            qa[key] = chosen_value
            save_job_qa(qa)
            print(f"[apply] auto-picked {auto_match['text']!r} for experience question: {q['text']}")
        elif {o['text'].strip().lower() for o in q['options']} == {"yes", "no"}:
            # Remaining Yes/No job-specific questions are qualification/skill
            # checks (degree, "do you know language X", etc.) — default to Yes
            # rather than pausing on Discord for each one.
            yes_option = next(o for o in q['options'] if o['text'].strip().lower() == "yes")
            chosen_value = yes_option['value']
            qa[key] = chosen_value
            save_job_qa(qa)
            print(f"[apply] defaulting to 'Yes' for qualification question: {q['text']}")
        else:
            options_desc = ", ".join(o['text'] for o in q['options'])
            reply = ask_discord_question(
                f"New job-specific question I haven't seen before:\n\"{q['text']}\"\n"
                f"Options: {options_desc}\nReply with the exact option text (e.g. \"Yes\")."
            )
            match = next(
                (o for o in q['options'] if o['text'].strip().lower() == reply.strip().lower()),
                None
            )
            if not match:
                raise RuntimeError(f"Couldn't match reply {reply!r} to any option for: {q['text']}")
            chosen_value = match['value']
            qa[key] = chosen_value
            save_job_qa(qa)
        set_select2_answer(driver, q['questionId'], chosen_value)
        time.sleep(0.5)


def answer_work_eligibility(driver):
    for name, value in WORK_ELIGIBILITY_ANSWERS.items():
        selector = f'input[type="radio"][name="{name}"][value="{value}"]'

        radio = None
        timer = 0
        while timer < 10:
            matches = driver.find_elements(By.CSS_SELECTOR, selector)
            if matches:
                radio = matches[0]
                break
            time.sleep(1)
            timer += 1
        if radio is None:
            print(f"[apply] Work Eligibility field {name!r} not present on this job's form, skipping.")
            continue

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", radio)

        # Bootstrap's custom-radio styling renders a <label> visually on top of
        # the real <input>, so a direct click on the input gets intercepted by
        # the label and throws ElementClickInterceptedException. Click the
        # label instead — same as a real user — with the input itself and then
        # a JS-forced set as fallbacks, none of which should ever crash the run.
        radio_id = radio.get_attribute("id")
        labels = driver.find_elements(By.CSS_SELECTOR, f'label[for="{radio_id}"]') if radio_id else []
        for clickable in (labels[0] if labels else None, radio):
            if clickable is None:
                continue
            try:
                clickable.click()
                break
            except Exception:
                continue
        time.sleep(0.3)

        if not driver.execute_script("return arguments[0].checked;", radio):
            # Force the state and fire the events a real click would produce.
            driver.execute_script(
                "arguments[0].checked = true;"
                "arguments[0].dispatchEvent(new Event('click', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                radio
            )
            time.sleep(0.3)

        if not driver.execute_script("return arguments[0].checked;", radio):
            print(f"[apply] WARNING: couldn't confirm {name}={value} got selected.")


def click_continue(driver):
    buttons = driver.find_elements(
        By.XPATH, '//div[@role="tabpanel" and contains(@class,"active")]//button[normalize-space(.)="Continue"]'
    )
    if not buttons:
        return False
    prev_panel = get_active_panel(driver)
    prev_id = prev_panel['id'] if prev_panel else None
    buttons[0].click()
    timer = 0
    while timer < 15:
        panel = get_active_panel(driver)
        if not panel or panel['id'] != prev_id:
            return True
        time.sleep(1)
        timer += 1
    raise TimeoutError("Clicked Continue but the panel never changed.")


def find_submit_button(driver):
    els = driver.find_elements(By.XPATH, '//button[contains(normalize-space(.), "Submit")]')
    return els[0] if els else None


def submit_job_application(driver, job):
    driver.get(job["url_next_step"])
    wait_until(driver, page_loaded=True)
    time.sleep(2)

    job_url = f"https://www.amazon.jobs{job['job_path']}"

    if "result=duplicate" in driver.current_url:
        print(f"[apply] already applied to {job['id_icims']}, skipping.")
        return

    # Step budget: generous upper bound on how many panels this wizard can have,
    # so a stuck/unexpected panel can't spin the loop forever.
    for _ in range(15):
        panel = get_active_panel(driver)
        if not panel:
            break
        if panel['label'] == "Job-specific questions":
            answer_job_specific_questions(driver, panel['id'])
        elif panel['label'] == "Work Eligibility":
            answer_work_eligibility(driver)
        # Other panels (contact info, general questions, education, resume,
        # acknowledgement, ID verification, EEO/disability/veteran self-ID)
        # already carry over pre-filled/completed from the candidate profile.

        if find_submit_button(driver):
            break
        if not click_continue(driver):
            break

    screenshot_path = debug_dump(driver, f"before_submit_{job['id_icims']}")
    send_discord(
        f"Ready to submit: **{job['title']}** — {job['normalized_location']}\n{job_url}",
        path=screenshot_path,
    )
    reply = ask_discord_question("Reply +submit to actually submit this application, or +skip to leave it for you to finish manually.")

    if reply.strip().lower().lstrip('+') != 'submit':
        send_discord(f"Skipped — left open for you to finish: {job['url_next_step']}")
        return

    submit_btn = find_submit_button(driver)
    if not submit_btn:
        send_discord("Couldn't find the Submit button — application left open for you to finish.")
        return

    submit_btn.click()
    wait_until(driver, page_loaded=True)
    time.sleep(2)
    debug_dump(driver, f"submitted_{job['id_icims']}")
    send_discord(f"Submitted: **{job['title']}** — {job_url}")


def dump_full_page(driver, step_dir):
    makedirs(step_dir, exist_ok=True)
    with open(f"{step_dir}/page.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    driver.save_screenshot(f"{step_dir}/screenshot.png")
    print(f"[capture] saved {step_dir} url={driver.current_url}")


def capture_application_flow(driver, start_url):
    driver.get(start_url)
    wait_until(driver, page_loaded=True)
    time.sleep(2)

    step = 0
    while True:
        step_dir = f"{CAPTURE_DIR}/step_{step:02d}"
        dump_full_page(driver, step_dir)
        cmd = input(
            f"[capture] Step {step} saved to {step_dir}. Click through the next step "
            "(or answer/type in the browser) then press Enter here to capture it, "
            "or type 'done' to stop: "
        ).strip().lower()
        if cmd == "done":
            break
        step += 1


def run_capture():
    makedirs(AMAZON_DIR, exist_ok=True)
    if not options['show']:
        print("[capture] forcing a visible browser — capture mode requires you to click through manually.")
        options['show'] = True

    driver = browser_options(True)
    try:
        start_url = options['job_url']
        job = None
        if not start_url:
            seen_ids = load_seen_ids()
            jobs = [j for j in search_sde2_jobs() if j["id_icims"] not in seen_ids]
            if not jobs:
                print("No unseen SDE2 jobs found to use for capture.")
                return
            job = jobs[0]
            start_url = job["url_next_step"]
            print(f"[capture] using job: {job['title']} ({job['normalized_location']}) {start_url}")

        login(driver, target_url=start_url)

        if "result=duplicate" in driver.current_url:
            print(f"[capture] already applied to this job, url={driver.current_url}")
            if job is not None:
                seen_ids = load_seen_ids()
                seen_ids.add(job["id_icims"])
                save_seen_ids(seen_ids)
                print("[capture] marked as seen so future runs pick a different job.")
            return

        capture_application_flow(driver, start_url)

        if job is not None:
            seen_ids = load_seen_ids()
            seen_ids.add(job["id_icims"])
            save_seen_ids(seen_ids)

        print(f"[capture] done. Review the dumps under {CAPTURE_DIR}/")
    except Exception:
        driver.save_screenshot(join(AMAZON_DIR, "debug_crash.png"))
        print(f"[debug] crashed at url={driver.current_url} title={driver.title!r}")
        traceback.print_exc()
        input("Something went wrong. Browser left open for you to inspect it — "
              "press Enter here once you're done to close it...")
    finally:
        driver.quit()


def send_discord(content, path=None, wait_for_otp=False):
    # bot.run() closes its event loop on exit; since this gets called once per
    # job notification, force a fresh loop each time or subsequent calls die
    # with "Event loop is closed".
    asyncio.set_event_loop(asyncio.new_event_loop())

    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Bot(intents=intents)

    @bot.event
    async def on_ready():
        channel = bot.get_channel(DISC_CHANNEL)
        if path:
            with open(path, mode='rb') as f:
                await channel.send(content=content, file=discord.File(f))
        else:
            await channel.send(content=content)
        if not wait_for_otp:
            await bot.close()

    if wait_for_otp:
        @bot.event
        async def on_message(message):
            channel = bot.get_channel(DISC_CHANNEL)
            if bot.user == message.author:
                return
            if message.content.startswith("+"):
                global OTP_CODE
                OTP_CODE = message.content
                await channel.send(f"Got your OTP code {OTP_CODE}, thanks.")
                await bot.close()
            else:
                await channel.send(f"Nope, please enter it starting with a + sign. Like +{message.content}")

    bot.run(DISC_KEY)


def browser_options(undetected=False):
    headless = True
    if options['show']:
        headless = False

    chrome_options = Options()

    if headless:
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
    else:
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')

    # undetected-chromedriver already handles anti-automation patching itself;
    # its bundled chromedriver rejects these plain-Selenium stealth options outright.
    if not undetected:
        chrome_options.add_experimental_option(
            "excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

    # Stop Chrome from autofilling saved credentials into the login form —
    # with a persistent profile, autofill + send_keys duplicates the typed text.
    chrome_options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2
    })

    makedirs(CHROME_PROFILE_DIR, exist_ok=True)

    if undetected:
        driver = uc.Chrome(suppress_welcome=False, options=chrome_options,
                            user_data_dir=CHROME_PROFILE_DIR)
    else:
        chrome_options.add_argument(f'--user-data-dir={CHROME_PROFILE_DIR}')
        driver = webdriver.Chrome(service=Service(
            ChromeDriverManager().install()), options=chrome_options)

    return driver


def wait_until(driver, seconds=0, jquery="", js="", url="", title="", text="", button="", page_loaded=False, max_wait=30):

    def sleeper(timer):
        time.sleep(timer)
        return timer

    if page_loaded:
        timer = 0
        is_page_loaded = False
        while not is_page_loaded and timer < max_wait:
            is_page_loaded = driver.execute_script(
                'return document.readyState;')
            if is_page_loaded == "complete":
                return True
            timer += sleeper(1)
        raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')

    if seconds:
        # wait for 'seconds' till continue
        time.sleep(seconds)
        return True

    if jquery:
        # wait till 'jquery' was executed succesfully
        is_on_page = False
        timer = 0
        while not is_on_page and timer < max_wait:
            is_on_page = driver.execute_script("""return """ + jquery)
            if is_on_page:
                return True
            timer += sleeper(1)

        raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')

    if js:
        # wait till 'js' was executed succesfully
        is_on_page = False
        timer = 0
        while not is_on_page and timer < max_wait:
            is_on_page = driver.execute_script('return ' + js)
            if is_on_page:
                return True
            timer += sleeper(1)

        raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')

    if url:
        # wait till certain 'url' is the current url or if part of the url is 'url'
        # - for example you can pass "login", if "login" is in url then continue
        is_new_page = False
        is_current_page = False
        timer = 0
        if url.startswith("not_"):
            url = url.split("not_")[1]
            while not is_new_page and timer < max_wait:
                if url not in driver.current_url:
                    is_new_page = True
                timer += sleeper(1)
        else:
            while not is_current_page and timer < max_wait:
                if url in driver.current_url:
                    is_current_page = True
                timer += sleeper(1)

        if is_new_page or is_current_page:
            return True
        else:
            raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')

    if title:
        # if the title of the website is "title" or includes "title"
        is_new_title = False
        is_current_title = False
        timer = 0
        if title.startswith("not_"):
            title = title.split("not_")[1]
            while not is_new_title and timer < max_wait:
                if title not in driver.title:
                    is_new_title = True
                timer += sleeper(1)
        else:
            while not is_current_title and timer < max_wait:
                if title in driver.title:
                    is_current_title = True
                timer += sleeper(1)

        if is_new_title or is_current_title:
            return True
        else:
            raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')

    if text:
        # is visible on page, this has to be an exact match
        is_text_on_page = False
        timer = 0
        while not is_text_on_page and timer < max_wait:
            try:
                try:
                    is_text_on_page = driver.execute_script(
                        f"return (document.documentElement.textContent || document.documentElement.innerText).indexOf('{text}') > -1")
                except Exception as e:
                    is_text_on_page = driver.execute_script(
                        f"return document.body.innerHTML.search('{text}') > -1")
            except Exception as e:
                if text in driver.page_source:
                    is_text_on_page = True
            timer += sleeper(1)

        if is_text_on_page:
            return True
        else:
            raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')

    if button:
        # check if button is visible on page
        is_on_page = False
        timer = 0
        while not is_on_page and timer < max_wait:
            if button.startswith("css_"):
                button_css_selector = button.split("css_")[1]
                try:
                    is_on_page = driver.find_element(
                        By.CSS_SELECTOR, button_css_selector)
                except Exception as e:
                    is_on_page = False

            elif button.startswith("xpath_"):
                button_xpath_selector = button.split("xpath_")[1]
                try:
                    #driver.find_element(By.XPATH, "//*[contains(text(), 'My Button')]")
                    is_on_page = driver.find_element(
                        By.XPATH, button_xpath_selector)
                except Exception as e:
                    is_on_page = False
            else:
                is_on_page = driver.execute_script(button)

            timer += sleeper(1)

        if is_on_page:
            return True
        else:
            raise TimeoutError(f'Max wait of {max_wait} seconds, exceeded.')


if __name__ == '__main__':
    my_parser = ArgumentParser()
    my_parser.add_argument('-s', '--show',
                           action='store_true',
                           help='Show browser. Default is headless.')
    my_parser.add_argument('--dry-run',
                           action='store_true',
                           help='Only search and print new SDE2 US jobs, no browser/login/Discord.')
    my_parser.add_argument('--posted-within-days',
                           type=int,
                           default=2,
                           help='Only include jobs posted within this many days (default 2).')
    my_parser.add_argument('--capture',
                           action='store_true',
                           help='Interactive capture mode: log in, open a job application, and dump '
                                'full HTML/screenshots at each step as you click through manually.')
    my_parser.add_argument('--job-url',
                           type=str,
                           default=None,
                           help='Specific job application URL to use with --capture '
                                '(defaults to the first new SDE2 match).')

    options = my_parser.parse_args()
    options = vars(options)

    if options['capture']:
        run_capture()
    else:
        main()
