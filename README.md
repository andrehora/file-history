# file-history

## Local preview

The app routes on the path, so refreshing a deep link like `/file/README.md`
asks for a file that is not on disk. GitHub Pages answers those with
`404.html`; `python -m http.server` answers with its own error page, so use
this instead:

    python3 -c "
    import http.server as h, os, re
    R = re.compile(r'/(file|dir|extension|all)/[^/?#]+/?$')
    class H(h.SimpleHTTPRequestHandler):
        def send_head(self):
            path = self.path.split('?')[0]
            if not os.path.exists(self.translate_path(path)) and R.search(path): self.path = '/index.html'
            return super().send_head()
    h.test(H, port=8000)"

## Data

The scripts live in `src/` and every one of them reads and writes under
`data/`, which is not tracked. A script moves to the repository root before it
starts, so it can be run from anywhere:

    src/top_repos.py         ->  data/top_repos.csv
    src/repo_files_today.py  ->  data/repo_files_today/
    src/repo_files_hist.py   ->  data/repo_files_<year>/
    src/repo_files_top.py    ->  data/top_files_<snapshot>.csv
    src/summarize.py         ->  data/summary_<snapshot>/
    src/merge_summary.py     ->  data/summary/
    src/build_index.py       ->  data.js

The page loads its table from `data.js`, which `build_index.py` writes from
`data/summary/*.csv`. That one stays at the top level, beside the page that
loads it; run it whenever the summaries change:

    python3 src/build_index.py

`404.html` is a copy of `index.html` — `build_index.py` re-copies it, but do so
by hand after editing `index.html` on its own, or deep-link refreshes will serve
a stale page.
