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

The page loads its table from `data.js`, which `build_index.py` writes from
`summary/*.csv`; run it whenever the summaries change:

    python3 build_index.py

`404.html` is a copy of `index.html` — `build_index.py` re-copies it, but do so
by hand after editing `index.html` on its own, or deep-link refreshes will serve
a stale page.
