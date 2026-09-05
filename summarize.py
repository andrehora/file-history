#!/usr/bin/env python3
"""Summarize the per repository listings written by the repo_files_* scripts.

Reads every CSV in an input directory and counts, for each name, how many
repositories contain it. A name repeated inside one repository counts once for
that repository, so the frequency is a count of repositories, not of entries:
it answers "how many of these projects have a README.md?" rather than "how many
README.md files exist in total".

Every directory named <INPUT_PREFIX><suffix> is summarized on its own, so the
snapshots of repo_files_hist.py and the current listing of repo_files_today.py
each get their own summary directory, <OUTPUT_PREFIX><suffix>:

    repo_files_2015/  -> summary_2015/
    repo_files_2020/  -> summary_2020/
    repo_files_today/ -> summary_today/

Three summaries are written into each one, with a name, a frequency and a
ratio column (the whole-number percentage of the repositories in that
snapshot), ordered by frequency:

    summary_2015/files.csv       file names, e.g. README.md
    summary_2015/dir.csv         directory names, e.g. src
    summary_2015/extension.csv   file extensions, e.g. .py

Usage:
    python summarize.py
"""

import csv
import os
import sys
from collections import Counter

# --- Settings -------------------------------------------------------------
INPUT_PREFIX = "repo_files_"  # every <prefix><suffix> directory is summarized
OUTPUT_PREFIX = "summary_"  # its summary goes to <prefix><suffix>
FILES_OUTPUT = "files.csv"
DIRS_OUTPUT = "dir.csv"
EXTENSIONS_OUTPUT = "extension.csv"
# --------------------------------------------------------------------------

FIELDS = ["name", "frequency", "ratio"]


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


def input_dirs(prefix=INPUT_PREFIX):
    """The listing directories to summarize, as (suffix, path), oldest name first."""
    return sorted(
        (entry.name[len(prefix):], entry.path)
        for entry in os.scandir(".")
        if entry.is_dir() and entry.name.startswith(prefix)
    )


def summarize(directory):
    """Count how many repositories contain each file, directory and extension."""
    listings = sorted(
        entry.path for entry in os.scandir(directory) if entry.name.endswith(".csv")
    )

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


def write_csv(counter, repos, path):
    """Write a counter as name,frequency,ratio, most frequent first.

    The ratio is the percentage of the repository listings in that snapshot
    carrying the name, rounded to a whole number, so 96 means 96% of the
    projects had it that year.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        # Ties are broken by name so that repeated runs give identical files.
        for name, frequency in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
            writer.writerow([name, frequency, round(100 * frequency / repos)])


def main():
    directories = input_dirs()
    if not directories:
        sys.exit(
            f"No {INPUT_PREFIX}* directories here: "
            "run repo_files_today.py or repo_files_hist.py first."
        )

    for suffix, directory in directories:
        # An empty directory is skipped rather than fatal, so one snapshot that
        # has not been fetched yet does not stop the others being summarized.
        repos, files, dirs, extensions = summarize(directory)
        if not repos:
            print(f"No CSV files in {directory}/, skipped.", file=sys.stderr)
            continue

        output_dir = f"{OUTPUT_PREFIX}{suffix}"
        print(
            f"Read {repos} repository listings from {directory}/ -> {output_dir}/",
            file=sys.stderr,
        )
        for counter, output in (
            (files, FILES_OUTPUT),
            (dirs, DIRS_OUTPUT),
            (extensions, EXTENSIONS_OUTPUT),
        ):
            path = os.path.join(output_dir, output)
            write_csv(counter, repos, path)
            print(f"{len(counter)} distinct names -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
