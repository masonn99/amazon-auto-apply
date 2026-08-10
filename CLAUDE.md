# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file (`amazon_job.py`) Selenium bot that finds new SDE2 job postings on amazon.jobs, logs into the user's Amazon.jobs account (OTP read automatically from Gmail via IMAP — no human in the loop), fills out the multi-step application form, and submits it — fully autonomously, up to a self-imposed active-application cap. Discord is still used for submission notifications and for asking about unrecognized job-specific questions, but no longer for OTP. There is no test suite, build step, or lint config; this is a personal automation script, not a package.

## Running it

```
pip3 install -r requirements.txt
python3 amazon_job.py                              # real run: search, login, fill, submit
python3 amazon_job.py --dry-run                     # search only, print matches, no browser/login/Discord
python3 amazon_job.py --show                        # run with a visible Chrome window instead of headless
python3 amazon_job.py --posted-within-days 7         # widen the "new jobs" window (default 2 days)
python3 amazon_job.py --check-applications           # log in, print active/cap count, exit
python3 amazon_job.py --capture                      # see "Capture mode" below
python3 amazon_job.py --capture --job-url "<url>"     # capture a specific page instead of the first new job
```

Requires a `.env` next to `amazon_job.py` (never commit this):
```
AMAZON_EM=<amazon.jobs email>
AMAZON_PW=<amazon.jobs password>
DISC_KEY=<discord bot token>
DISC_CHANNEL=<discord channel id>
```

No automated tests exist. Verify changes by running with `--show` and watching a real (or `--dry-run`) pass — see "Testing changes" below.

## Architecture

Everything lives in `amazon_job.py`, organized as one pipeline: **search → login → per-job fill/submit loop**, plus a separate **capture mode** used to reverse-engineer the site's DOM when the flow changes.

### 1. Job search (`search_sde2_jobs`)
Hits the public, unauthenticated `amazon.jobs/en/search.json` API directly — no browser needed for this part. Filters titles with `SDE2_TITLE_RE` / excludes `EXCLUDE_TITLE_RE` (drops Senior/III/Principal/Manager/Intern/Lead), then filters by `posted_date`. `main()` diffs the result against `amazon/seen_jobs.json` (persisted, id_icims-keyed) so re-runs only touch new postings.

### 2. Login (`login`)
`account.amazon.jobs` / `passport.amazon.jobs` is a server-rendered Rails app with React islands, not a SPA — pages are real navigations. `login(driver, target_url=...)` navigates straight to the *target* page (e.g. a specific job's apply URL) rather than the generic login page first, so Amazon's own post-auth redirect lands back on that target instead of the candidate homepage. If the persistent Chrome profile already has a valid session, Amazon skips the login form entirely and `login()` returns immediately (`"[login] already authenticated"`) — no OTP needed. When login *is* required, it fills the form via `set_input_value` (bypasses `send_keys` — see comment in code for why), submits, then resolves the OTP via `fetch_otp_from_gmail()`: polls Gmail (IMAP, `GMAIL_APP_PASSWORD`, required) for a new email from `noreply@mail.amazon.jobs` newer than `otp_request_ts` (captured *before* the submit click, with a 30s buffer — capturing it after risks rejecting the real code as "stale" if the email's server timestamp lands earlier than the capture point), regex-extracts the code. No Discord fallback — `login()` raises if Gmail doesn't produce a code within the timeout.

**Session persistence**: `CHROME_PROFILE_DIR` (and every other `amazon/...` path) is anchored to `dirname(abspath(__file__))`, not the shell's cwd — a cwd-relative path here would silently start a fresh empty profile (and force OTP again) on any run launched from a different directory. Even with a stable profile, Amazon's own auth cookie (`mons_auth` / `__Host-mons-sidp`) expires roughly every 24h server-side, so periodic OTP re-auth is expected behavior, not a bug.

### 3. Per-job apply loop (`submit_job_application`)
The application itself is a single page (`account.amazon.jobs/.../apply`) with every step's HTML already present in the DOM simultaneously — "steps" are just `<div role="tabpanel">` sections toggled via an `active` CSS class, not separate page loads. `get_active_panel()` reads whichever panel currently has `.active`; `click_continue()` clicks that panel's Continue button and waits for the active panel's `id` to change.

Most panels (Contact info, General questions, Education, Resume, Acknowledgement, ID verification, EEO/disability/veteran self-ID) carry over pre-filled and already "completed" from the candidate's profile — the code does not touch them. Only two panels need active handling:

- **"Job-specific questions"** (`answer_job_specific_questions`): varies per job posting. Each question is a select2 dropdown wrapping a hidden `<select>` (real `<option>` elements exist in the DOM; select2 just hides them behind a styled widget). Answered in priority order:
  1. `amazon/job_qa.json` cache (question text → chosen option value) — a question is only ever asked once, ever.
  2. `pick_experience_option`: if every option parses as a years/months range (Amazon's actual phrasing: `"less than X"`, `"X to less than Y"`, `"more than X"`), auto-picks the bucket containing `TARGET_EXPERIENCE_YEARS` (currently 3).
  3. `pick_yes_no_experience_answer`: Yes/No questions that embed a numeric threshold *and* the word "experience" (e.g. "Do you have 3+ years of ... experience?") — auto-answers based on the same target.
  4. Any other Yes/No question defaults to "Yes" (qualification/skill self-assessment questions — degree, "do you know language X", etc.).
  5. Anything else (a genuinely novel non-binary/non-range question) blocks on `ask_discord_question` and waits for a typed reply — this is the one remaining path that can stall an unattended run.
  All auto-resolved and asked answers get written back to `job_qa.json`, so this list of "known" questions grows over time and future runs ask less.

- **"Work Eligibility"** (`answer_work_eligibility`): two fields aren't already defaulted correctly from the profile and are hardcoded in `WORK_ELIGIBILITY_ANSWERS` (`REQUIRE_SPONSORSHIP: NO`, `GEF_EXT_USA_GOVERNMENT_EMPLOYEE: NEVER`). These are native `<input type="radio">` elements, but Bootstrap's custom-radio CSS renders the `<label>` visually on top of the actual input, so a direct Selenium click on the input throws `ElementClickInterceptedException` — click the associated `<label for="...">` instead (falls back to the raw input, then a JS-forced `checked`+event-dispatch, in case the label click also doesn't register).

Once no more Continue buttons are found (or a Submit button appears), `submit_job_application` clicks Submit directly and posts a Discord screenshot as a record — there is intentionally **no blocking confirmation gate** anymore; the active-application cap check (below) is what stands in for a safety net now.

### 4. Active-application cap (`get_active_application_count`, called from `main`)
Amazon caps active applications at 10 (`ACTIVE_APPLICATION_CAP`). The dashboard's own `"Active (N)"` tab (`#active-tab-desktop`, `aria-label="N of active applications"`) is read directly as ground truth rather than trying to classify each application card's status text. `main()` checks this once after login, then tracks it locally (incrementing per real submission) rather than re-fetching before every job. If the cap is already hit, remaining jobs for that run are **left unmarked** in `seen_jobs.json` so a future run retries them once there's room, instead of silently dropping them.

### Capture mode (`run_capture` / `capture_application_flow`)
Not part of the apply pipeline — a debugging tool for when Amazon changes the DOM and the selectors above start failing. `--capture [--job-url URL]` logs in, navigates to a job's apply page (or the given URL — also used to inspect e.g. the applications dashboard), then loops: dump full `driver.page_source` + a screenshot to `amazon/capture/step_NN/`, pause for the user to manually click/type in the visible browser, press Enter to capture the next step, `done` to stop. When the flow breaks, capture a fresh trace this way and read the HTML dumps before guessing at new selectors.

## Known limitations

- Job-specific questions that are neither a Yes/No nor a fully-parseable experience range still block indefinitely on a Discord reply — there's no timeout/skip. This is the one remaining path that can stall a fully unattended (e.g. cron) run even with Gmail OTP configured.
- `find_submit_button` matches any button containing "Submit" — broad by design since the exact final-review button hasn't been captured for every job variant.
- Assumes job-specific questions are always select2 dropdowns (true for every job seen so far); a radio-button-based job-specific question would need a new handler.
- `fetch_otp_from_gmail` matches on sender `noreply@mail.amazon.jobs` and the phrase `"code on amazon.jobs: <digits>"` — if Amazon changes that email's wording, `OTP_CODE_RE` needs updating; since there's no Discord fallback anymore, a mismatch here means `login()` raises instead of degrading gracefully.
