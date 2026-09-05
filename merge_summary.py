#!/usr/bin/env python3
"""Merge the per snapshot summaries written by summarize.py into one file per kind.

summarize.py writes a summary directory per snapshot (summary_2015/,
summary_2020/, summary_today/), each holding the same three CSVs. This script
puts the snapshots side by side, so a name can be followed over time:

    summary_2015/files.csv  \\
    summary_2020/files.csv   >  summary/files.csv
    summary_today/files.csv /

Each output has a name column followed by a frequency and a ratio column per
snapshot, named after its suffix and ordered oldest first:

    name,frequency_2015,ratio_2015,frequency_2020,ratio_2020,frequency_today,ratio_today
    README.md,2074,78,2631,84,2712,87

A name gets a row when the newest snapshot has it in more than MIN_FREQUENCY
repositories, which keeps the outputs to the names common enough today to say
something and drops the long tail of one-off names; a name kept that way still
shows its real frequency in the older snapshots, where it may be rare or
absent. A name missing from a snapshot has 0 in both of that snapshot's columns, which is the
truth for these summaries (a name absent from a summary was in no repository of
that snapshot). Rows are ordered by the newest ratio, most common first.

Usage:
    python merge_summary.py
"""

import csv
import os
import sys

# --- Settings -------------------------------------------------------------
INPUT_PREFIX = "summary_"  # every <prefix><suffix> directory is merged in
OUTPUT_DIR = "summary"  # the merged CSVs go here
OUTPUTS = ["files.csv", "dir.csv", "extension.csv"]  # merged one by one
MIN_FREQUENCY = 100  # a name is kept when the newest snapshot beats this; 0 keeps everything
# --------------------------------------------------------------------------

MISSING = 0  # written for a name that a snapshot does not have


def input_dirs(prefix=INPUT_PREFIX):
    """The summary directories to merge, as (suffix, path), oldest name first."""
    return sorted(
        (entry.name[len(prefix):], entry.path)
        for entry in os.scandir(".")
        if entry.is_dir() and entry.name.startswith(prefix) and entry.name != OUTPUT_DIR
    )


def read_summary(path):
    """Return {name: (frequency, ratio)} for one summary CSV, or None if absent.

    A snapshot that has not been summarized yet is missing the file rather than
    empty, and the two mean different things, so they are kept apart here.
    """
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as handle:
        return {
            row["name"]: (row["frequency"], row["ratio"]) for row in csv.DictReader(handle)
        }


def merge(summaries, minimum=MIN_FREQUENCY):
    """Rows of name plus a frequency and ratio per snapshot, most frequent first.

    `summaries` is the (suffix, {name: (frequency, ratio)}) of each snapshot;
    the columns follow that order, so the caller decides how time runs.

    A name is kept when the newest snapshot, the last one given, has it above
    `minimum`; a name that was common once and has since died out is therefore
    left out. The threshold only decides which names get a row: a name kept on
    the strength of the newest snapshot still shows its real, possibly small,
    frequency in the older ones.
    """
    _, newest = summaries[-1]
    names = {
        name for name, (frequency, _) in newest.items() if int(frequency) > minimum
    }

    rows = []
    for name in names:
        row = [name]
        for _, summary in summaries:
            row.extend(summary.get(name, (MISSING, MISSING)))
        rows.append(row)

    # Ordered by the newest ratio, so the file starts with what is most common
    # now. That ratio is a whole number and ties are common, so the newest
    # frequency separates them, and the name after that, which also makes
    # repeated runs give identical files.
    newest_frequency = len(summaries) * 2 - 1
    rows.sort(key=lambda row: (-int(row[newest_frequency + 1]), -int(row[newest_frequency]), row[0]))
    return rows


def main():
    directories = input_dirs()
    if not directories:
        sys.exit(f"No {INPUT_PREFIX}* directories here: run summarize.py first.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for output in OUTPUTS:
        summaries = []
        for suffix, directory in directories:
            summary = read_summary(os.path.join(directory, output))
            # A snapshot without this summary is left out of the columns
            # entirely, rather than filling them all with zeros.
            if summary is None:
                print(f"No {output} in {directory}/, skipped.", file=sys.stderr)
                continue
            summaries.append((suffix, summary))

        if not summaries:
            print(f"No {output} in any {INPUT_PREFIX}* directory.", file=sys.stderr)
            continue

        header = ["name"]
        for suffix, _ in summaries:
            header.extend([f"frequency_{suffix}", f"ratio_{suffix}"])

        rows = merge(summaries)
        path = os.path.join(OUTPUT_DIR, output)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        snapshots = ", ".join(suffix for suffix, _ in summaries)
        print(f"{len(rows)} distinct names from {snapshots} -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
