# Amazon Job Apply

Originally used on the German Amazon job site to apply to a single, hardcoded job. This fork instead searches amazon.jobs for **new SDE2 (Software Development Engineer II) postings in the USA**, fills out the application (job-specific questions, work eligibility), and submits it automatically — stopping only when Amazon's 10-active-application cap is hit — and posts a screenshot to Discord as a record of each submission. Use it at your own risk, I am not gonna be responsible if you get blocked or don't get a job because of this.

**IMPORTANT! I do assume you have an Amazon job account as well as an Amazon account and have already filled out and uploaded your resume.**
This script is kind of cool, but I didnt write it for the masses. I do think the `wait_until` function is kind of neat for anything Selenium.

<p align="center">
  <img src="discordbotshot.png" alt="Discordbot example" width="400"/>
</p>

## How it works
1. Queries the public `amazon.jobs` search API for US postings matching SDE2-level titles (excludes Senior/III/Principal/Manager/Intern/Lead).
2. Filters to only jobs posted within the last `N` days (default 2, override with `--posted-within-days`), computed dynamically from today's date each run.
3. Compares against `amazon/seen_jobs.json` to find only postings not already handled by a previous run.
4. Without `--dry-run`, it logs into your Amazon account (OTP is read automatically out of Gmail — `GMAIL_APP_PASSWORD` is required), then for each new job fills out and **submits** the application, posting a screenshot to Discord afterward.
5. Job-specific questions are answered automatically: experience-range dropdowns pick the bucket containing `TARGET_EXPERIENCE_YEARS` (default 3), Yes/No qualification questions default to Yes, and anything else pauses and asks on Discord once — the answer is cached in `amazon/job_qa.json` so it's never asked twice. Work Eligibility answers (sponsorship, government employment) are hardcoded in `WORK_ELIGIBILITY_ANSWERS`.
6. Before each run, it checks Amazon's own "Active (N)" applications count and stops once you're at the 10-application cap, leaving remaining matches unmarked so a future run picks them up once you have room.
7. The job is marked "seen" either way so you won't get notified about it again — even if it turned out to be a duplicate application or the cap was already hit.

## Scripts

Everything runs through the single entry point `amazon_job.py`:

| Command | What it does |
|---|---|
| `python3 amazon_job.py` | Real run: search → login → fill → submit, headless by default. |
| `python3 amazon_job.py --dry-run` | Search only, print matches. No browser, no login, no Discord — safe to run anytime. |
| `python3 amazon_job.py --dry-run --posted-within-days 7` | Same as above, widening the "new jobs" window (default 2 days). |
| `python3 amazon_job.py --show` / `-s` | Real run with a visible Chrome window instead of headless. |
| `python3 amazon_job.py --check-applications` | Log in, print the current active/cap application count, and exit — no applying. |
| `python3 amazon_job.py --capture` | Interactive capture mode: log in, open a job application, and dump full HTML/screenshots at each step as you click through manually. Used to reverse-engineer the DOM when Amazon changes the flow. |
| `python3 amazon_job.py --capture --job-url "<url>"` | Same as above, but against a specific URL instead of the first new job match. |

`--posted-within-days N` can be combined with any real (non-capture) run to widen/narrow the search window.

## Setup
```
pip3 install -r requirements.txt
```
You WILL have to set up a Discord Bot for this, I mainly have to do this because I use MFA for everything (you should too) and in order to solve it from anywhere in the world Discord seemed like an easy solution. The `pycord`library I use makes it tricky to send messages with other sripts (like you cant import send and use it, because bot.run will block..so I had to use the hacky way)

#### Chromedriver 
I give you 2 options here: either you use undetected-chromedriver (for serious stuff I'd recommend) OR you use ChromeDriverManager which will to all the boring update and download of chromedriver for you.

### .env
Yours should look like this (except with your values) and be in the same directory as `amazon_job.py`.
```
AMAZON_EM=bot.daddy@evil.fail
AMAZON_PW=Password123
DISC_KEY=hcbueb3uifnot.real.h3oHURHOE.cj3uh94hnci.efjeunekncvioeunvuiernvo
DISC_CHANNEL=9893859notreal3283245
GMAIL_EM=bot.daddy@evil.fail
GMAIL_APP_PASSWORD=abcdabcdabcdabcd
```
`GMAIL_APP_PASSWORD` is required — OTP is read directly out of Gmail via IMAP, there's no Discord fallback. To set it up: your Google account needs 2-Step Verification enabled, then generate a 16-character App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (this is **not** your regular Gmail password — Google doesn't accept that for IMAP). `GMAIL_EM` defaults to `AMAZON_EM` if not set separately. Discord (`DISC_KEY`/`DISC_CHANNEL`) is still used for submission notifications and for asking about job-specific questions it hasn't seen before.

### Directory Structure
the script auto-creates an `amazon/` directory for screenshots, the persistent Chrome profile, `seen_jobs.json`, and `job_qa.json`; the `.env` and `amazon_job.py` need to be in the same directory.
```
projekt > amazon_job.py, .env, amazon/
```
