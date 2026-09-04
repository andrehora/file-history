#!/usr/bin/env python3
"""List the files of every repository in top_repos.csv using the GitHub API.

Repositories are read from the CSV produced by top_repos.py; for each one the
default branch is resolved and its git tree is walked recursively, giving the
full file listing in a single request per repository. Each listing is written
to its own CSV under OUTPUT_DIR, as <owner>__<name>.csv.

Requests go through the token pool of top_repos.py, so GH_TOKEN may hold
several tokens separated by commas or whitespace; they are rotated so that a
token hitting its rate limit hands off to the next one.

Settings live in the constants below; edit them and run the script.

Usage:
    export GH_TOKEN=ghp_aaa,ghp_bbb   # optional, but strongly recommended
    python repo_files.py
"""

import csv
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from top_repos import TokenPool, parse_tokens, request_json

API_ROOT = "https://api.github.com"

# --- Settings -------------------------------------------------------------
INPUT = "top_repos.csv"
OUTPUT_DIR = "tmp_repo_files"  # one CSV per repository is written in here
LIMIT = 0  # how many repositories to process; 0 means all of them
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


def list_files(pool, full_name):
    """Return one row per file and directory in `full_name`, at its default branch.

    With ASCII_ONLY set, names carrying anything outside plain ASCII are left
    out of the listing entirely.

    Returns None when the API call failed, which is not the same as a
    repository that genuinely has no files: an empty list gets written out as
    an empty CSV, a failure does not. An empty repository has no HEAD and
    answers 409, which counts as a failure here.

    The recursive tree endpoint returns the whole repository in one response,
    but caps it at 100k entries / 7MB; when that happens GitHub sets
    `truncated` and the listing is incomplete rather than wrong.
    """
    try:
        # HEAD resolves to the default branch, so the file listing costs one
        # request per repository rather than two.
        tree = request_json(
            pool,
            f"{API_ROOT}/repos/{full_name}/git/trees/HEAD",
            {"recursive": "1"},
        )
    # 404/451 (missing, renamed, DMCA'd) raise; so does a request that ran out
    # of retries. Neither should take the whole run down.
    except (requests.HTTPError, RuntimeError) as exc:
        log(f"{full_name}: {exc}")
        return None

    if tree.get("truncated"):
        log(f"{full_name}: tree truncated by the API; listing is partial")

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
            log(f"{full_name}: skipped {len(rows) - len(kept)} non-ASCII names")
        return kept

    return rows


def csv_path(full_name, directory=OUTPUT_DIR):
    """Where the listing of one repository is stored: <dir>/<owner>__<name>.csv."""
    return os.path.join(directory, f"{full_name.replace('/', '__')}.csv")


def write_csv(rows, path):
    """Write one repository's file listing, creating the output directory."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def fetch_all(repos, tokens=None, workers=WORKERS):
    """List the files of every repository, writing one CSV each as they land.

    Returns the number of files written and the repositories that failed.
    """
    interval = AUTHENTICATED_INTERVAL if tokens else ANONYMOUS_INTERVAL
    pool = TokenPool(tokens, interval=interval)
    workers = workers or CONCURRENCY_PER_TOKEN * len(pool.sessions)

    total = 0
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        listings = executor.map(lambda name: list_files(pool, name), repos)
        for done, (full_name, rows) in enumerate(zip(repos, listings), start=1):
            if rows is None:
                failed.append(full_name)
                log(f"[{done}/{len(repos)}] {full_name}: failed, no CSV written")
                continue
            total += len(rows)
            path = csv_path(full_name)
            write_csv(rows, path)
            log(f"[{done}/{len(repos)}] {len(rows)} files -> {path}")

    return total, failed


def main():
    tokens = parse_tokens(os.environ.get("GH_TOKEN"))
    if tokens:
        print(f"Using {len(tokens)} token(s) from GH_TOKEN.", file=sys.stderr)
    else:
        print(
            "No GH_TOKEN set: anonymous access allows only 60 requests/hour, "
            "so at most ~60 repositories before everything starts failing.",
            file=sys.stderr,
        )

    repos = read_repos(INPUT)
    log(f"Listing files for {len(repos)} repositories from {INPUT}.")

    total, failed = fetch_all(repos, tokens=tokens, workers=WORKERS)

    log(f"Wrote {total} files for {len(repos) - len(failed)} repositories -> {OUTPUT_DIR}/")
    if failed:
        log(f"{len(failed)} repositories failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
