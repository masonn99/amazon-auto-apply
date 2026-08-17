import json
import re
import time
from argparse import ArgumentParser
from datetime import date, datetime, timedelta
from os import makedirs
from os.path import abspath, dirname, exists, join

import requests


# Microsoft's careers site runs on Eightfold AI ("PCS" product) as of the
# Nov 2025 migration — this is Eightfold's public, unauthenticated search
# endpoint, same role as amazon.jobs/en/search.json in amazon_job.py. Its
# smart filters (reverse-engineered from pcsxPwa.js — they're plain
# filter_<name> query params, not a single JSON "filters" blob) narrow the
# result set server-side to ~70 postings instead of the ~500-650 a free-text
# query like "Software Engineer II" pulls back (Eightfold's search matches
# loosely on individual words, not the phrase). Fewer pages means less risk
# of the rate limit below, and the title regex still does the precise
# SDE2-equivalent match on top of this.
MS_SEARCH_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
MS_DOMAIN = "microsoft.com"
SEARCH_LOCATION = "United States"
SEARCH_FILTERS = {
    "seniority": "Mid-Level",
    "career_discipline": "Software Engineering",
}
# Unlike amazon.jobs/en/search.json, this endpoint 429s under rapid
# back-to-back pagination requests with no browser-like User-Agent — a plain
# requests default UA plus no delay was enough to trigger it in testing.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}
PAGINATION_DELAY_SECONDS = 1.5

# Microsoft frequently posts a single combined listing spanning both the II
# and Senior levels (e.g. "Software Engineer II & Senior Software Engineer"),
# unlike Amazon where each level gets its own posting. So — unlike
# amazon_job.py's EXCLUDE_TITLE_RE — "Senior" alone is not excluded here; only
# the explicit "II"/"2" marker is required, which already doesn't match plain
# "Senior Software Engineer" postings that lack that marker entirely.
SDE2_TITLE_RE = re.compile(r"(?i)\bsoftware engineer\s*(ii|2)\b")
EXCLUDE_TITLE_RE = re.compile(r"(?i)\b(principal|partner|director|manager|intern|lead|iii)\b")

MICROSOFT_DIR = join(dirname(abspath(__file__)), "microsoft")
SEEN_JOBS_PATH = join(MICROSOFT_DIR, "seen_jobs.json")


def search_sde2_jobs(min_posted_date=None):
    jobs_by_id = {}
    params = {
        "domain": MS_DOMAIN,
        "query": "",
        "location": SEARCH_LOCATION,
        "start": 0,
    }
    params.update({f"filter_{name}": value for name, value in SEARCH_FILTERS.items()})

    start = 0
    while True:
        params["start"] = start
        response = requests.get(MS_SEARCH_URL, params=params, headers=REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()["data"]
        positions = data.get("positions", [])
        if not positions:
            break
        for job in positions:
            jobs_by_id[job["id"]] = job
        start += len(positions)
        if start >= data.get("count", 0):
            break
        time.sleep(PAGINATION_DELAY_SECONDS)

    matched = [
        job for job in jobs_by_id.values()
        if SDE2_TITLE_RE.search(job.get("name", ""))
        and not EXCLUDE_TITLE_RE.search(job.get("name", ""))
    ]

    if min_posted_date is not None:
        matched = [
            job for job in matched
            if (posted := parse_posted_ts(job.get("postedTs"))) is not None
            and posted >= min_posted_date
        ]

    return matched


def parse_posted_ts(value):
    if not value:
        return None
    return datetime.fromtimestamp(value).date()


def job_url(job):
    return f"https://apply.careers.microsoft.com{job['positionUrl']}"


def load_seen_ids():
    if not exists(SEEN_JOBS_PATH):
        return set()
    with open(SEEN_JOBS_PATH) as f:
        return set(json.load(f))


def save_seen_ids(seen_ids):
    makedirs(MICROSOFT_DIR, exist_ok=True)
    with open(SEEN_JOBS_PATH, "w") as f:
        json.dump(sorted(seen_ids), f, indent=2)


def main():
    cutoff = date.today() - timedelta(days=options['posted_within_days'])
    seen_ids = load_seen_ids()
    jobs = search_sde2_jobs(min_posted_date=cutoff)
    new_jobs = [job for job in jobs if job["id"] not in seen_ids]

    if not new_jobs:
        print(f"No new SDE2-equivalent Microsoft jobs found in the US posted on/after {cutoff}.")
        return

    print(f"Found {len(new_jobs)} new job(s) posted on/after {cutoff}:")
    for job in new_jobs:
        print(f" - {job['name']} ({', '.join(job['standardizedLocations'])}) {job_url(job)}")

    if options['dry_run']:
        return

    seen_ids.update(job["id"] for job in new_jobs)
    save_seen_ids(seen_ids)


if __name__ == '__main__':
    my_parser = ArgumentParser()
    my_parser.add_argument('--dry-run',
                            action='store_true',
                            help='Print new matches without marking them seen.')
    my_parser.add_argument('--posted-within-days',
                            type=int,
                            default=2,
                            help='Only include jobs posted within this many days (default 2).')

    options = my_parser.parse_args()
    options = vars(options)

    main()
