#!/usr/bin/env python3
"""Fetch the top 1000 most starred repositories from the GitHub API.

The GitHub Search API caps any single query at 1000 results (10 pages of 100),
so larger pulls are done by walking down the star axis: each time a query is
exhausted the next one is capped at the lowest star count already seen.

GITHUB_TOKEN may hold several tokens separated by commas or whitespace; they
are rotated so that a token hitting its rate limit hands off to the next one.

Settings live in the constants below; edit them and run the script.

Usage:
    export GITHUB_TOKEN=ghp_aaa,ghp_bbb   # optional, but strongly recommended
    python top_repos.py
"""

import csv
import os
import re
import sys
import time

import requests

API_URL = "https://api.github.com/search/repositories"
PER_PAGE = 100
MAX_PAGES = 10  # 10 * 100 = 1000, the Search API hard limit

# --- Settings -------------------------------------------------------------
LIMIT = 100  # how many repositories to fetch (may exceed 1000, see fetch_top_repos)
MIN_STARS = 1000  # ignore repositories below this star count
EXTRA_QUALIFIERS = ""  # optional extra search terms, e.g. "language:python"
OUTPUT = "top_repos.csv"  # output CSV file path
# --------------------------------------------------------------------------

FIELDS = [
    "full_name",
    "stargazers_count",
    "language",
    "topics",
]


def parse_tokens(raw):
    """Split a GITHUB_TOKEN value into individual tokens (comma/whitespace separated)."""
    if not raw:
        return []
    return [token for token in re.split(r"[,\s]+", raw.strip()) if token]


class TokenPool:
    """Round-robin over one or more tokens, skipping ones that are rate limited.

    With no tokens at all the pool still yields a single anonymous session, so
    the script keeps working (just against the much lower anonymous quota).
    """

    def __init__(self, tokens):
        self.tokens = tokens or [None]
        self.sessions = [self._build_session(token) for token in self.tokens]
        self.blocked_until = [0.0] * len(self.tokens)
        self.index = 0

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

    def acquire(self):
        """Return (index, session) for the next usable token, waiting if all are blocked."""
        now = time.time()
        for offset in range(len(self.sessions)):
            index = (self.index + offset) % len(self.sessions)
            if self.blocked_until[index] <= now:
                self.index = (index + 1) % len(self.sessions)
                return index, self.sessions[index]

        index = min(range(len(self.sessions)), key=lambda i: self.blocked_until[i])
        wait = max(self.blocked_until[index] - now, 1)
        print(f"All {len(self.sessions)} token(s) rate limited; sleeping {wait:.0f}s...", file=sys.stderr)
        time.sleep(wait)
        self.blocked_until[index] = 0.0
        self.index = (index + 1) % len(self.sessions)
        return index, self.sessions[index]

    def block(self, index, until):
        self.blocked_until[index] = until


def request_page(pool, page, query, max_retries=5):
    """Fetch one page of search results, retrying on rate limits and 5xx."""
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": PER_PAGE,
        "page": page,
    }

    for attempt in range(max_retries):
        index, session = pool.acquire()
        try:
            response = session.get(API_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"Request error ({exc}); retrying...", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue

        if response.ok:
            return response.json()

        # 403/429 with no remaining quota means this token hit its rate limit:
        # park it until its reset and let the next token take over.
        if response.status_code in (403, 429):
            if response.headers.get("X-RateLimit-Remaining") == "0":
                reset = float(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                pool.block(index, reset + 1)
            else:
                retry_after = float(response.headers.get("Retry-After", 2 ** attempt))
                pool.block(index, time.time() + retry_after)
            continue

        if response.status_code >= 500:
            time.sleep(2 ** attempt)
            continue

        response.raise_for_status()

    raise RuntimeError(f"Giving up on page {page} after {max_retries} attempts")


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


def fetch_top_repos(limit=1000, min_stars=MIN_STARS, tokens=None):
    """Fetch the `limit` most starred repositories, newest star count first.

    A single search query can only return 1000 results, so once a window is
    exhausted we start a new one capped at the lowest star count seen so far
    (`stars:MIN..LAST`). Windows overlap on their boundary star count, so
    results are deduplicated by full name. Forked repositories are skipped.
    """
    pool = TokenPool(tokens)
    repos = []
    seen = set()
    max_stars = None  # star ceiling of the current window; None = no ceiling

    while len(repos) < limit:
        query = build_query(min_stars, max_stars)
        window_added = 0
        last_stars = None

        for page in range(1, MAX_PAGES + 1):
            data = request_page(pool, page, query)
            items = data.get("items", [])
            if not items:
                break

            for repo in items:
                if repo["full_name"] in seen:
                    continue
                seen.add(repo["full_name"])
                repos.append(extract_fields(repo))
                window_added += 1
                if len(repos) >= limit:
                    break

            last_stars = items[-1]["stargazers_count"]
            print(f"[{query}] page {page}: {len(repos)} repositories", file=sys.stderr)
            if len(repos) >= limit or len(items) < PER_PAGE:
                break
            # Search API allows 30 req/min authenticated, 10 unauthenticated.
            time.sleep(1 if pool.authenticated else 6)

        if len(repos) >= limit or last_stars is None or last_stars <= min_stars:
            break
        if window_added == 0:
            # Everything in this window was already collected: more than 1000
            # repositories share this star count, so we cannot page past it.
            print(f"Stuck at {last_stars} stars; stopping early.", file=sys.stderr)
            break
        max_stars = last_stars
        time.sleep(1 if pool.authenticated else 6)

    return repos[:limit]


def write_csv(repos, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(repos)


def main():
    tokens = parse_tokens(os.environ.get("GITHUB_TOKEN"))
    if tokens:
        print(f"Using {len(tokens)} token(s) from GITHUB_TOKEN.", file=sys.stderr)
    else:
        print("No GITHUB_TOKEN set: using the slower unauthenticated limit.", file=sys.stderr)

    repos = fetch_top_repos(limit=LIMIT, min_stars=MIN_STARS, tokens=tokens)
    write_csv(repos, OUTPUT)
    print(f"Fetched {len(repos)} repositories -> {OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
