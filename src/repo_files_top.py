#!/usr/bin/env python3
"""Combine the per repository listings into one file per snapshot, keeping only
the names common enough to be worth studying.

summarize.py answers "how many repositories have a README.md?" but throws away
which ones they are. The full listings keep that relation and are far too large
to handle as one file, so this script keeps the relation for the popular names
only: every row of the output says that one repository had one name.

A name is kept when more than MIN_FREQUENCY repositories of some snapshot carry
it. The test is made across all snapshots rather than the newest one, so a name
that was common in 2015 and has since faded stays in the 2015 relation instead
of leaving a hole there.

One CSV is written per <INPUT_PREFIX><suffix> directory:

    data/repo_files_2015/  -> data/top_files_2015.csv
    data/repo_files_today/ -> data/top_files_today.csv

with a repository, a name and a type column, ordered by repository and then by
name:

    repo,name,type
    torvalds/linux,.gitignore,file
    torvalds/linux,Makefile,file

Usage:
    python src/repo_files_top.py
"""

import csv
import os
import sys
from collections import Counter

# --- Settings -------------------------------------------------------------
# Every script reads and writes under here, so the repository keeps its
# generated data in one place.
# The scripts live in src/ and the data beside it, so a run moves to the
# repository root first and every path below is read from there, whatever
# directory the script was started from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
DATA_DIR = "data"
INPUT_PREFIX = "repo_files_"  # every <prefix><suffix> directory is combined
OUTPUT_PREFIX = "top_files_"  # its relation goes to <prefix><suffix>.csv
MIN_FREQUENCY = 100  # a name is kept when a snapshot beats this; 0 keeps everything
# --------------------------------------------------------------------------

FIELDS = ["repo", "name", "type"]


def input_dirs(prefix=INPUT_PREFIX):
    """The listing directories to combine, as (suffix, path), oldest name first."""
    # Nothing has been fetched yet when the data directory is not there; that
    # is the caller's message to write, not a traceback.
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(
        (entry.name[len(prefix):], entry.path)
        for entry in os.scandir(DATA_DIR)
        if entry.is_dir() and entry.name.startswith(prefix)
    )


def listings(directory):
    """The listings of one snapshot, as (repository, [(name, type), ...])."""
    for entry in sorted(os.scandir(directory), key=lambda e: e.name):
        if not entry.name.endswith(".csv"):
            continue
        # The file name carries the repository: <owner>__<name>.csv.
        repo = entry.name[:-len(".csv")].replace("__", "/", 1)
        with open(entry.path, newline="", encoding="utf-8") as handle:
            yield repo, [(row["name"], row["type"]) for row in csv.DictReader(handle)]


def popular(directories, minimum=MIN_FREQUENCY):
    """The (name, type) pairs carried by more than `minimum` repositories somewhere.

    Counting is a pass of its own: holding every listing in memory to avoid it
    would cost gigabytes, while the counts are a few million small tuples.
    """
    keep = set()
    for suffix, directory in directories:
        freq = Counter()
        repos = 0
        for _, rows in listings(directory):
            repos += 1
            # A name repeated inside one repository counts once for it, the same
            # rule summarize.py uses.
            freq.update(set(rows))
        above = {row for row, count in freq.items() if count > minimum}
        keep |= above
        print(
            f"{directory}/: {repos} listings, {len(freq)} names, {len(above)} above {minimum}",
            file=sys.stderr,
        )
    return keep


def write_relation(directory, keep, path):
    """Write one snapshot's repository-to-name relation, popular names only."""
    kept = total = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        for repo, rows in listings(directory):
            total += len(rows)
            for name, kind in sorted(set(rows)):
                if (name, kind) in keep:
                    writer.writerow([repo, name, kind])
                    kept += 1
    return total, kept


def main():
    directories = input_dirs()
    if not directories:
        sys.exit(
            f"No {INPUT_PREFIX}* directories in {DATA_DIR}/: "
            "run repo_files_today.py or repo_files_hist.py first."
        )

    keep = popular(directories)
    print(f"{len(keep)} names kept in all", file=sys.stderr)

    for suffix, directory in directories:
        path = os.path.join(DATA_DIR, f"{OUTPUT_PREFIX}{suffix}.csv")
        total, kept = write_relation(directory, keep, path)
        share = 100 * kept / total if total else 0
        print(
            f"{kept} of {total} rows ({share:.1f}%) -> {path} "
            f"[{os.path.getsize(path) / 1e6:.1f} MB]",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
