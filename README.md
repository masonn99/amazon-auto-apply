# Amazon Job Apply
Originally used on the German Amazon job site to apply to a single, hardcoded job. This fork instead searches amazon.jobs for **new SDE2 (Software Development Engineer II) postings in the USA** and notifies you on Discord to review/finish each one — it does not auto-submit. Use it at your own risk, I am not gonna be responsible if you get blocked or don't get a job because of this.

**IMPORTANT! I do assume you have an Amazon job account as well as an Amazon account and have already filled out and uploaded your resume.**
This script is kind of cool, but I didnt write it for the masses. I do think the `wait_until` function is kind of neat for anything Selenium.

<p align="center">
  <img src="discordbotshot.png" alt="Discordbot example" width="400"/>
</p>

## How it works
1. Queries the public `amazon.jobs` search API for US postings matching SDE2-level titles (excludes Senior/III/Principal/Manager/Intern/Lead).
2. Filters to only jobs posted within the last `N` days (default 2, override with `--posted-within-days`), computed dynamically from today's date each run.
3. Compares against `amazon/seen_jobs.json` to find only postings not already handled by a previous run.
4. Run with `--dry-run` to just print matches — no browser, no login, no Discord, safe to run anytime:
   ```
   python3 amazon_job.py --dry-run
   python3 amazon_job.py --dry-run --posted-within-days 7
   ```
5. Without `--dry-run`, it logs into your Amazon account (MFA/OTP relayed via Discord, same as before), then for each new job opens its real application page, takes a screenshot, and posts it to Discord with a direct link.
6. **It stops there and does not click final submit** — Amazon's real US application flow is multi-step and untested against automation, so you review and finish each one yourself. The job is marked "seen" either way so you won't get notified about it again.
7. There's no way to filter for "no visa sponsorship" in the job search itself — that's a question inside the application form you answer yourself when you get there.

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
```

### Directory Structure
the script auto-creates an `amazon/` directory for screenshots and `seen_jobs.json`; the `.env` and `amazon_job.py` need to be in the same directory.
```
projekt > amazon_job.py ,.env , amazon/
```

