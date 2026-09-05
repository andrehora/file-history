#!/usr/bin/env python3
"""List the files of every repository in top_repos.csv as they were on past dates.

This is the historical counterpart of repo_files_today.py: instead of the current
default branch, each repository is listed at the last commit made before each
date in DATES. Resolving that commit costs one extra request, so a repository
is two requests per date (commit lookup, then tree).

Listings are written one CSV per repository per date, under a directory named
after the year of the date, as <owner>__<name>.csv:

    repo_files_2015/torvalds__linux.csv
    repo_files_2020/torvalds__linux.csv

A repository that already has a CSV for a date is left alone, so an interrupted
run can simply be started again; delete the CSV (or the directory) to refresh it.

Repositories created after a date have no commit before it; they are reported
as absent for that date and no CSV is written, which is deliberately different
from an empty CSV (a repository that existed and had no listable files).

Requests go through the token pool of top_repos.py, so GH_TOKEN may hold
several tokens separated by commas or whitespace; they are rotated so that a
token hitting its rate limit hands off to the next one.

Settings live in the constants below; edit them and run the script.

Usage:
    export GH_TOKEN=ghp_aaa,ghp_bbb   # optional, but strongly recommended
    python repo_files_hist.py
"""

import csv
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from top_repos import TokenPool, parse_tokens, request_json

API_ROOT = "https://api.github.com"

# --- Settings -------------------------------------------------------------
INPUT = "top_repos.csv"
# The snapshots to take, as ISO dates; each repository is listed at its last
# commit strictly before the date. The year names the output directory, so two
# dates in the same year would collide.
DATES = [
    # "2015-01-01",
    # "2020-01-01",
    "2026-01-01",
]
OUTPUT_PREFIX = "repo_files_"  # output directory is <prefix><year>
LIMIT = 0  # how many repositories to process; 0 means all of them
SKIP_EXISTING = True  # leave repositories that already have a CSV for the date alone
ASCII_ONLY = True  # drop entries whose name is not plain ASCII
WORKERS = 0  # concurrent requests; 0 means CONCURRENCY_PER_TOKEN per token
# --------------------------------------------------------------------------

FIELDS = [
    "name",
    "type",
]

# The core API allows 5000 requests/hour per token, only 60 when anonymous.
# Unlike the Search API there is no per-minute cap to pace against, so requests
# are fired as fast as the secondary limits allow (roughly 15/second) and a
# token that does run out is parked on its 403 until the hourly reset.
AUTHENTICATED_INTERVAL = 0.1
ANONYMOUS_INTERVAL = 1.0
CONCURRENCY_PER_TOKEN = 4

# Workers log as they finish, so the shared stderr is guarded.
print_lock = threading.Lock()


def log(message):
    with print_lock:
        print(message, file=sys.stderr)


def read_repos(path, limit=LIMIT):
    """Read the repository names from the top_repos.csv output."""
    with open(path, newline="", encoding="utf-8") as handle:
        names = [row["full_name"] for row in csv.DictReader(handle)]
    return names[:limit] if limit else names


def output_dir(date, prefix=OUTPUT_PREFIX):
    """The directory holding the listings for one date: <prefix><year>."""
    return f"{prefix}{date[:4]}"


def resolve_commit(pool, full_name, date):
    """Return the sha of the last commit on the default branch before `date`.

    Returns None when the repository had no commit that early, which normally
    means it did not exist yet.
    """
    # No `sha` parameter, so the listing follows the default branch; `until` is
    # inclusive on the timestamp, and per_page=1 keeps the response small.
    commits = request_json(
        pool,
        f"{API_ROOT}/repos/{full_name}/commits",
        {"until": f"{date}T00:00:00Z", "per_page": 1},
    )
    if not commits:
        return None
    return commits[0]["sha"]


def list_files(pool, full_name, date):
    """Return one row per file and directory in `full_name`, as of `date`.

    With ASCII_ONLY set, names carrying anything outside plain ASCII are left
    out of the listing entirely.

    Returns None when the API call failed and the string "absent" when the
    repository had no commit before `date`; neither is the same as a
    repository that genuinely has no files, which gets an empty list and so an
    empty CSV. An empty repository has no commits at all and answers 409, which
    counts as a failure here.

    The recursive tree endpoint returns the whole repository in one response,
    but caps it at 100k entries / 7MB; when that happens GitHub sets
    `truncated` and the listing is incomplete rather than wrong.
    """
    try:
        sha = resolve_commit(pool, full_name, date)
        if sha is None:
            return "absent"
        tree = request_json(
            pool,
            f"{API_ROOT}/repos/{full_name}/git/trees/{sha}",
            {"recursive": "1"},
        )
    # 404/451 (missing, renamed, DMCA'd) raise; so does a request that ran out
    # of retries. Neither should take the whole run down.
    except (requests.HTTPError, RuntimeError) as exc:
        log(f"{full_name} @ {date}: {exc}")
        return None

    if tree.get("truncated"):
        log(f"{full_name} @ {date}: tree truncated by the API; listing is partial")

    # "blob" is a file and "tree" a directory; "commit" is a submodule, which
    # is neither, so it is left out.
    kinds = {"blob": "file", "tree": "dir"}
    rows = [
        # The API gives the path from the repository root; only the last
        # segment is kept, so the same name in two directories appears twice.
        {"name": entry["path"].rsplit("/", 1)[-1], "type": kinds[entry["type"]]}
        for entry in tree.get("tree", [])
        if entry.get("type") in kinds
    ]

    if ASCII_ONLY:
        kept = [row for row in rows if row["name"].isascii()]
        if len(kept) != len(rows):
            log(f"{full_name} @ {date}: skipped {len(rows) - len(kept)} non-ASCII names")
        return kept

    return rows


def csv_path(full_name, directory):
    """Where the listing of one repository is stored: <dir>/<owner>__<name>.csv."""
    return os.path.join(directory, f"{full_name.replace('/', '__')}.csv")


def write_csv(rows, path):
    """Write one repository's file listing, creating the output directory."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def fetch_date(repos, date, pool, workers):
    """List every repository at one date, writing one CSV each as they land.

    Returns the number of files written, the repositories that failed and the
    ones that did not exist yet.
    """
    directory = output_dir(date)
    total = 0
    failed = []
    absent = []

    # A run interrupted halfway leaves the CSVs it did write in place; those
    # repositories are not fetched again, so a rerun picks up where it stopped.
    if SKIP_EXISTING:
        skipped = {name for name in repos if os.path.exists(csv_path(name, directory))}
        if skipped:
            log(f"{date}: skipping {len(skipped)} repositories already listed in {directory}/")
        repos = [name for name in repos if name not in skipped]
        if not repos:
            return total, failed, absent

    log(f"{date}: fetching {len(repos)} repositories with {workers} workers")

    # Results are handled as they land rather than in submission order, so one
    # slow repository (a parked token, a retry) cannot hold back the log.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(list_files, pool, name, date): name for name in repos
        }
        for done, future in enumerate(as_completed(futures), start=1):
            full_name = futures[future]
            rows = future.result()
            if rows is None:
                failed.append(full_name)
                log(f"[{date} {done}/{len(repos)}] {full_name}: failed, no CSV written")
                continue
            if rows == "absent":
                absent.append(full_name)
                log(f"[{date} {done}/{len(repos)}] {full_name}: no commit before {date}")
                continue
            total += len(rows)
            path = csv_path(full_name, directory)
            write_csv(rows, path)
            log(f"[{date} {done}/{len(repos)}] {len(rows)} files -> {path}")

    return total, failed, absent


def fetch_all(repos, dates=DATES, tokens=None, workers=WORKERS):
    """List every repository at every date, one date after the other.

    Returns a dict of date -> (files written, failed repositories, absent ones).
    """
    interval = AUTHENTICATED_INTERVAL if tokens else ANONYMOUS_INTERVAL
    pool = TokenPool(tokens, interval=interval)
    workers = workers or CONCURRENCY_PER_TOKEN * len(pool.sessions)

    results = {}
    for date in dates:
        log(f"--- {date} -> {output_dir(date)}/ ---")
        results[date] = fetch_date(repos, date, pool, workers)
    return results


def main():
    tokens = parse_tokens(os.environ.get("GH_TOKEN"))
    if tokens:
        print(f"Using {len(tokens)} token(s) from GH_TOKEN.", file=sys.stderr)
    else:
        print(
            "No GH_TOKEN set: anonymous access allows only 60 requests/hour, "
            "and each repository costs two requests per date, so at most ~30 "
            "repositories before everything starts failing.",
            file=sys.stderr,
        )

    repos = read_repos(INPUT)
    log(f"Listing files for {len(repos)} repositories from {INPUT} at {len(DATES)} dates.")

    results = fetch_all(repos, tokens=tokens, workers=WORKERS)

    any_failed = False
    for date, (total, failed, absent) in results.items():
        listed = len(repos) - len(failed) - len(absent)
        log(f"{date}: wrote {total} files for {listed} repositories -> {output_dir(date)}/")
        if absent:
            log(f"{date}: {len(absent)} repositories had no commit yet")
        if failed:
            any_failed = True
            log(f"{date}: {len(failed)} repositories failed: {', '.join(failed)}")

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
