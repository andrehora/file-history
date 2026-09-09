#!/usr/bin/env python3
"""Write the app's data file from the merged summaries.

merge_summary.py writes data/summary/{files,dir,extension}.csv, one row per name with
a frequency and a ratio per snapshot. The app reads that table as docs/data.js,
a single `var DATA = ...;` the page loads before its own script, so there is no
fetch to make and no loading state to draw. This script writes docs/data.js from
the CSVs and copies docs/index.html over docs/404.html, which GitHub Pages
serves for deep links and so has to stay identical.

Run it after merge_summary.py, whenever the summaries change:

    python src/build_index.py
"""

import csv
import json
import os
import shutil
import sys

# --- Settings -------------------------------------------------------------
# Every script reads and writes under here, so the repository keeps its
# generated data in one place.
# The scripts live in src/ and the data beside it, so a run moves to the
# repository root first and every path below is read from there, whatever
# directory the script was started from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
DATA_DIR = "data"
INPUT_DIR = os.path.join(DATA_DIR, "summary")  # where merge_summary.py leaves its CSVs
SOURCES = [("files.csv", "file"), ("dir.csv", "dir"), ("extension.csv", "extension")]
# The site is what GitHub Pages serves, and it serves a folder: the page, its
# copy for deep links and the table it loads all live in here together.
SITE_DIR = "docs"
DATA_FILE = os.path.join(SITE_DIR, "data.js")  # the table, loaded by the page
PAGE = os.path.join(SITE_DIR, "index.html")  # the app itself
DEEP_LINK_PAGE = os.path.join(SITE_DIR, "404.html")  # a copy of it, served for deep links
# --------------------------------------------------------------------------


def snapshots(header):
    """The snapshot suffixes of a merged header, oldest first."""
    return [c[len("frequency_"):] for c in header if c.startswith("frequency_")]


def read_source(path, kind):
    """The rows of one merged CSV as the items the app draws."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        years = snapshots(reader.fieldnames or [])
        if not years:
            sys.exit("%s: no frequency_<snapshot> columns" % path)
        items = []
        for row in reader:
            items.append({
                "n": row["name"],
                "k": kind,
                "f": [int(row["frequency_" + y]) for y in years],
                "r": [int(row["ratio_" + y]) for y in years],
            })
    return years, items


def main():
    years, items = None, []
    for name, kind in SOURCES:
        path = os.path.join(INPUT_DIR, name)
        if not os.path.exists(path):
            sys.exit("missing %s; run merge_summary.py first" % path)
        found, rows = read_source(path, kind)
        if years is None:
            years = found
        elif found != years:
            sys.exit("%s covers %s, expected %s" % (path, found, years))
        items.extend(rows)
        print("%s: %d %s" % (path, len(rows), kind))

    data = json.dumps({"years": years, "items": items}, separators=(",", ":"))
    open(DATA_FILE, "w", encoding="utf-8").write("var DATA = " + data + ";\n")
    shutil.copyfile(PAGE, DEEP_LINK_PAGE)
    print("%s: %d names over %s" % (DATA_FILE, len(items), ", ".join(years)))
    print("%s: copied to %s" % (PAGE, DEEP_LINK_PAGE))


if __name__ == "__main__":
    main()
