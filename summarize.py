#!/usr/bin/env python3
"""Summarize the per repository listings written by repo_files.py.

Reads every CSV in INPUT_DIR and counts, for each name, how many repositories
contain it. A name repeated inside one repository counts once for that
repository, so the frequency is a count of repositories, not of entries: it
answers "how many of these projects have a README.md?" rather than "how many
README.md files exist in total".

Three summaries are written into OUTPUT_DIR, each with a name and a frequency
column, ordered by frequency:

    summary/files.csv       file names, e.g. README.md
    summary/dir.csv         directory names, e.g. src
    summary/extension.csv   file extensions, e.g. .py

Usage:
    python summarize.py
"""

import csv
import os
import sys
from collections import Counter

# --- Settings -------------------------------------------------------------
INPUT_DIR = "tmp_repo_files"
OUTPUT_DIR = "summary"
FILES_OUTPUT = "files.csv"
DIRS_OUTPUT = "dir.csv"
EXTENSIONS_OUTPUT = "extension.csv"
# --------------------------------------------------------------------------

FIELDS = ["name", "frequency"]


def read_listing(path):
    """Return the names in one repository listing, split by kind.

    Duplicates are collapsed here, which is what makes the totals a count of
    repositories: a repository with forty __init__.py files still contributes
    one to that name.
    """
    files = set()
    dirs = set()
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            target = files if row["type"] == "file" else dirs
            target.add(row["name"])
    return files, dirs


def extension(name):
    """The extension of a file name, or None when it has none.

    A leading dot is part of the name rather than an extension, so .gitignore
    counts as a file without one; a name with several dots keeps only the last
    piece, so archive.tar.gz is a .gz file.
    """
    ext = os.path.splitext(name)[1]
    return ext or None


def summarize(directory=INPUT_DIR):
    """Count how many repositories contain each file, directory and extension."""
    listings = sorted(
        entry.path for entry in os.scandir(directory) if entry.name.endswith(".csv")
    )
    if not listings:
        sys.exit(f"No CSV files in {directory}/: run repo_files.py first.")

    files = Counter()
    dirs = Counter()
    extensions = Counter()
    for path in listings:
        repo_files, repo_dirs = read_listing(path)
        files.update(repo_files)
        dirs.update(repo_dirs)
        # Extensions are deduplicated per repository as well, so a repository
        # full of .py files adds one to .py.
        extensions.update({ext for ext in map(extension, repo_files) if ext})

    return len(listings), files, dirs, extensions


def write_csv(counter, path):
    """Write a counter as name,frequency, most frequent first."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        # Ties are broken by name so that repeated runs give identical files.
        writer.writerows(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def main():
    repos, files, dirs, extensions = summarize(INPUT_DIR)
    print(f"Read {repos} repository listings from {INPUT_DIR}/.", file=sys.stderr)

    for counter, output in (
        (files, FILES_OUTPUT),
        (dirs, DIRS_OUTPUT),
        (extensions, EXTENSIONS_OUTPUT),
    ):
        path = os.path.join(OUTPUT_DIR, output)
        write_csv(counter, path)
        print(f"{len(counter)} distinct names -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
