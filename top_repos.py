#!/usr/bin/env python3
"""Fetch the top 1000 most starred repositories from the GitHub API.

The GitHub Search API caps any single query at 1000 results (10 pages of 100),
so larger pulls are done by walking down the star axis: each time a query is
exhausted the next one is capped at the lowest star count already seen.

GH_TOKEN may hold several tokens separated by commas or whitespace; they
are rotated so that a token hitting its rate limit hands off to the next one.

Settings live in the constants below; edit them and run the script.

Usage:
    export GH_TOKEN=ghp_aaa,ghp_bbb   # optional, but strongly recommended
    python top_repos.py
"""

import csv
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

API_URL = "https://api.github.com/search/repositories"
PER_PAGE = 100
MAX_PAGES = 10

# --- Settings -------------------------------------------------------------
LIMIT = 10000
MIN_STARS = 500
EXTRA_QUALIFIERS = ""
OUTPUT = "top_repos.csv"
WORKERS = 0  # concurrent requests; 0 means one worker per token
# --------------------------------------------------------------------------

FIELDS = [
    "full_name",
    "stargazers_count",
    "language",
    "topics",
]


def parse_tokens(raw):
    """Split a GH_TOKEN value into individual tokens (comma/whitespace separated)."""
    if not raw:
        return []
    return [token for token in re.split(r"[,\s]+", raw.strip()) if token]


class TokenPool:
    """Round-robin over one or more tokens, skipping ones that are rate limited.

    With no tokens at all the pool still yields a single anonymous session, so
    the script keeps working (just against the much lower anonymous quota).
    """

    def __init__(self, tokens, interval=None):
        self.tokens = tokens or [None]
        # Endpoint specific pacing; None falls back to the Search API limits.
        self._interval = interval
        self.sessions = [self._build_session(token) for token in self.tokens]
        # Earliest time each token may be used again, so that concurrent
        # workers cannot burn through a single token's quota.
        self.free_at = [0.0] * len(self.tokens)
        # The latest "everything is rate limited" wait already reported.
        self._announced_until = 0.0
        self.lock = threading.Lock()

    @staticmethod
    def _build_session(token):
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "top-repos-script",
            }
        )
        if token:
            session.headers["Authorization"] = f"Bearer {token}"
        return session

    @property
    def authenticated(self):
        return any(token for token in self.tokens)

    @property
    def interval(self):
        """Minimum seconds between two uses of the same token.

        The Search API allows 30 requests/minute per token, 10 when anonymous;
        other endpoints pace differently and pass their own interval in.
        """
        if self._interval is not None:
            return self._interval
        return 2.0 if self.authenticated else 6.0

    def acquire(self):
        """Reserve the token that comes free soonest, waiting for it if needed.

        Thread safe: the reservation is made under the lock, the waiting is
        done outside it so other workers can keep claiming other tokens.
        """
        with self.lock:
            index = min(range(len(self.sessions)), key=lambda i: self.free_at[i])
            start = max(self.free_at[index], time.time())
            self.free_at[index] = start + self.interval
        wait = start - time.time()
        if wait > 0:
            # A short wait is ordinary pacing; a long one means every token is
            # rate limited, which otherwise looks exactly like a hang. Every
            # worker is waiting on the same reset, so only the first says so.
            if wait > 30 and self._announce_wait(start):
                print(
                    f"All tokens are rate limited; waiting {int(wait)}s for the "
                    "next reset.",
                    file=sys.stderr,
                )
            time.sleep(wait)
        return index, self.sessions[index]

    def _announce_wait(self, until):
        """True when this long wait has not been reported yet."""
        with self.lock:
            if until <= self._announced_until:
                return False
            self._announced_until = until
            return True

    def block(self, index, until):
        """Park a rate limited token until its quota resets.

        Returns True only the first time a token is pushed out to a given
        reset; concurrent workers all get the same 403 and would otherwise
        each report the same park.
        """
        with self.lock:
            if until <= self.free_at[index]:
                return False
            self.free_at[index] = until
            return True


def request_json(pool, url, params=None, max_retries=5):
    """Fetch one URL as JSON, retrying on rate limits and 5xx.

    Tokens are taken from the pool one request at a time, so a token that hits
    its limit is parked and the next one picks the work up.
    """
    attempt = 0
    while attempt < max_retries:
        index, session = pool.acquire()
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"Request error ({exc}); retrying...", file=sys.stderr)
            time.sleep(2 ** attempt)
            attempt += 1
            continue

        if response.ok:
            return response.json()

        # 403/429 with no remaining quota means this token hit its rate limit:
        # park it until its reset and let the next token take over.
        if response.status_code in (403, 429):
            if response.headers.get("X-RateLimit-Remaining") == "0":
                reset = float(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                # Without this the script simply goes quiet for up to an hour,
                # which is indistinguishable from a hang.
                if pool.block(index, reset + 1):
                    print(
                        f"Token {index + 1} is out of quota; parked for "
                        f"{max(0, int(reset - time.time()))}s (until its reset).",
                        file=sys.stderr,
                    )
                # Deliberately not counted as an attempt: the next acquire()
                # sleeps until a token is free again, and giving up here would
                # mark thousands of repositories as failed for no reason.
                continue
            else:
                retry_after = float(response.headers.get("Retry-After", 2 ** attempt))
                if pool.block(index, time.time() + retry_after):
                    print(
                        f"Token {index + 1} hit a secondary limit; "
                        f"backing off {retry_after:g}s.",
                        file=sys.stderr,
                    )
            attempt += 1
            continue

        if response.status_code >= 500:
            time.sleep(2 ** attempt)
            attempt += 1
            continue

        response.raise_for_status()

    raise RuntimeError(f"Giving up on {url} after {max_retries} attempts")


def request_page(pool, page, query, max_retries=5):
    """Fetch one page of search results."""
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": PER_PAGE,
        "page": page,
    }
    return request_json(pool, API_URL, params, max_retries)


def extract_fields(repo):
    """Keep the columns we care about; topics come back as a list."""
    row = {field: repo.get(field) for field in FIELDS}
    row["topics"] = ";".join(repo.get("topics") or [])
    return row


def build_query(min_stars, max_stars):
    """Build the search query for one star window."""
    if max_stars is None:
        stars = f"stars:>={min_stars}"
    else:
        stars = f"stars:{min_stars}..{max_stars}"
    # fork:false is the API default, but being explicit documents the intent.
    return f"{stars} fork:false {EXTRA_QUALIFIERS}".strip()


def fetch_window(pool, executor, query, pages):
    """Fetch `pages` pages of one query in parallel, returned in page order.

    The pages of a single query are independent of each other -- only the move
    to the *next* window depends on results -- so they can be fetched at once.
    """
    futures = [executor.submit(request_page, pool, page, query) for page in range(1, pages + 1)]
    return [future.result().get("items", []) for future in futures]


def fetch_top_repos(limit=1000, min_stars=MIN_STARS, tokens=None, workers=WORKERS):
    """Fetch the `limit` most starred repositories, most starred first.

    A single search query can only return 1000 results, so once a window is
    exhausted we start a new one capped at the lowest star count seen so far
    (`stars:MIN..LAST`). Windows overlap on their boundary star count, so
    results are deduplicated by full name. Forked repositories are skipped.

    Windows are walked sequentially (each ceiling comes from the previous
    window's results) but the pages inside a window are fetched concurrently.
    """
    pool = TokenPool(tokens)
    workers = workers or len(pool.sessions)
    repos = []
    seen = set()
    max_stars = None  # star ceiling of the current window; None = no ceiling

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while len(repos) < limit:
            query = build_query(min_stars, max_stars)
            pages = min(MAX_PAGES, math.ceil((limit - len(repos)) / PER_PAGE))
            window = fetch_window(pool, executor, query, pages)

            window_added = 0
            last_stars = None
            for items in window:
                if not items:
                    break
                for repo in items:
                    if repo["full_name"] in seen:
                        continue
                    seen.add(repo["full_name"])
                    repos.append(extract_fields(repo))
                    window_added += 1
                last_stars = items[-1]["stargazers_count"]

            print(f"[{query}] {len(repos)} repositories", file=sys.stderr)

            if len(repos) >= limit or last_stars is None or last_stars <= min_stars:
                break
            if window_added == 0:
                # Everything in this window was already collected: more than
                # 1000 repositories share this star count, so we cannot page
                # past it.
                print(f"Stuck at {last_stars} stars; stopping early.", file=sys.stderr)
                break
            max_stars = last_stars

    repos.sort(key=lambda repo: repo["stargazers_count"], reverse=True)
    return repos[:limit]


def write_csv(repos, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(repos)


def main():
    tokens = parse_tokens(os.environ.get("GH_TOKEN"))
    if tokens:
        print(f"Using {len(tokens)} token(s) from GH_TOKEN.", file=sys.stderr)
    else:
        print("No GH_TOKEN set: using the slower unauthenticated limit.", file=sys.stderr)

    repos = fetch_top_repos(limit=LIMIT, min_stars=MIN_STARS, tokens=tokens, workers=WORKERS)
    write_csv(repos, OUTPUT)
    print(f"Fetched {len(repos)} repositories -> {OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
