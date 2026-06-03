#!/usr/bin/env python3
"""
serve.py — tiny local server that powers the Refresh button.

Run this instead of opening the HTML directly:
    python3 serve.py
Then open http://localhost:8731 in your browser.

- GET  /            -> serves tree_decisions.html (scrapes once if missing)
- POST /refresh     -> re-runs tree_monitor.scrape(), regenerates the HTML,
                       returns 200. The page's Refresh button calls this.

Why: opening the file via file:// can't trigger a re-scrape (browsers can't
run Python). Serving it locally lets the Refresh button do a real refresh.
"""
import http.server, socketserver, os, traceback
import tree_monitor as tm

PORT = 8731
HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "tree_decisions.html")

def regenerate():
    rows = tm.assign_tiers(tm.scrape())
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(tm.render(rows))
    return len(rows)

class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        if self.path in ("/", "/index.html", "/tree_decisions.html"):
            if not os.path.exists(HTML):
                try: regenerate()
                except Exception as e:
                    return self._send(500, f"<pre>Scrape failed:\n{e}</pre>")
            with open(HTML, "rb") as f:
                return self._send(200, f.read())
        self._send(404, "not found")

    def do_POST(self):
        if self.path == "/refresh":
            try:
                n = regenerate()
                return self._send(200, f"ok {n}")
            except Exception:
                return self._send(500, "<pre>"+traceback.format_exc()+"</pre>")
        self._send(404, "not found")

    def log_message(self, *a):  # quiet
        pass

if __name__ == "__main__":
    print(f"Serving on http://localhost:{PORT}  (Ctrl+C to stop)")
    if not os.path.exists(HTML):
        try:
            print("Initial scrape…"); print("  rows:", regenerate())
        except Exception as e:
            print("  initial scrape failed:", e)
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
