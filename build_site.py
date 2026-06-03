#!/usr/bin/env python3
"""
Miami Tree-Removal Intended-Decision Monitor
============================================

Scrapes the City of Miami "View Intended Decisions Posted for Tree Permitting"
page, parses each decision, and writes a self-contained HTML report
(tree_decisions.html) with a working REFRESH button.

WHY A SCRIPT (not just an HTML page):
  - Browsers block cross-origin fetches to miami.gov, so a pure web page
    cannot pull the data itself.
  - The city's CDN also blocks plain bot traffic (HTTP 403). This script
    tries ordinary requests first and, if blocked, falls back to a headless
    browser (Playwright) that behaves like a real browser.

USAGE
  python3 tree_monitor.py                # scrape live + write tree_decisions.html, then open it
  python3 tree_monitor.py --no-open      # scrape, write, don't auto-open
  The generated HTML has a "Refresh" button that re-runs this script.

DAILY AUTOMATION (optional)
  macOS/Linux cron, every day at 7am:
    0 7 * * *  /usr/bin/python3 /full/path/tree_monitor.py --no-open
  Windows: Task Scheduler -> daily -> action: python tree_monitor.py --no-open

DATA CAVEAT
  The city pages describe trees in free prose and DO NOT label which trees are
  "specimen" or "prohibited" (they only say un-flagged trees are non-specimen).
  Those two columns therefore show "not stated" unless the page explicitly
  uses the words "specimen" or "prohibited". Counts of removals / relocations /
  prunings are parsed from phrases like "ONE (1) Mango, TWO (2) Oak".
"""

import sys, os, re, json, html, subprocess, datetime, webbrowser

INDEX_URL = ("https://www.miami.gov/My-Government/Departments/Building/"
             "View-Intended-Decisions-Posted-for-Tree-Permitting")
BASE = "https://www.miami.gov"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

WORDNUM = {
    "one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,
    "ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,
    "sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,
    "thirty":30,"forty":40,"fifty":50,
}

# ---------------------------------------------------------------------------
# Fetch layer: try requests, then Playwright headless browser as fallback.
# ---------------------------------------------------------------------------
def fetch_requests(url):
    import requests
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
    if r.status_code == 200 and len(r.text) > 500:
        return r.text
    raise RuntimeError(f"requests got HTTP {r.status_code} (len {len(r.text)})")

_PW = {"checked": False, "ok": False}
def fetch_playwright(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(user_agent=BROWSER_HEADERS["User-Agent"])
        pg.goto(url, wait_until="networkidle", timeout=45000)
        htmltext = pg.content()
        b.close()
        return htmltext

def fetch(url):
    """Return HTML for url, trying the cheapest method that works."""
    try:
        return fetch_requests(url)
    except Exception as e_req:
        try:
            return fetch_playwright(url)
        except Exception as e_pw:
            raise RuntimeError(
                f"Could not fetch {url}.\n"
                f"  requests: {e_req}\n"
                f"  playwright: {e_pw}\n"
                f"  Fix: run `pip install playwright && playwright install chromium`, "
                f"or run this script on a network that isn't blocked.")

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def to_int(token):
    token = token.strip().lower().replace(",", "")
    if token.isdigit():
        return int(token)
    return WORDNUM.get(token)

def count_in_phrase(phrase):
    """Count tree instances in prose like 'ONE (1) Mango, TWO(2) Oak'.
    Strategy: sum parenthetical digit groups '(N)'. If none, sum standalone
    leading digits per item. Returns int (0 if nothing found)."""
    if not phrase:
        return 0
    paren = re.findall(r'\((\d+)\)', phrase)
    if paren:
        return sum(int(x) for x in paren)
    # fallback: word-numbers or bare digits at item starts
    total = 0
    for m in re.finditer(r'\b(\d+|' + "|".join(WORDNUM) + r')\b', phrase, re.I):
        v = to_int(m.group(1))
        if v:
            total += v
    return total

# Field-label patterns -> bucket. These match the SPECIFIC list labels, e.g.
# "Tree(s) To Be Removed & Location:" — NOT the "General Description" summary.
# We require the word "Tree" near the action AND a following colon so we land
# on the itemized line, then skip any line that is the general description.
DESC_RE     = re.compile(r'general description', re.I)
REMOVE_RE   = re.compile(r'tree\(?s?\)?.{0,30}\bremov', re.I)
RELOCATE_RE = re.compile(r'tree\(?s?\)?.{0,30}\breloc', re.I)
PRUNE_RE    = re.compile(r'tree\(?s?\)?.{0,30}\bprun', re.I)
REPLACE_RE  = re.compile(r'replacement tree|number of replacement', re.I)

def grab(label_re, lines):
    """Return descriptive text after the matching itemized label line.
    Skips the 'General Description' summary line. Picks the line that
    actually carries a count (parenthetical number) when more than one
    candidate exists."""
    candidates = []
    for ln in lines:
        if DESC_RE.search(ln):
            continue
        if label_re.search(ln) and ":" in ln:
            after = ln.split(":", 1)[1].strip()
            candidates.append(after)
    if not candidates:
        return ""
    # prefer a candidate that contains a count like "(2)"
    for c in candidates:
        if re.search(r'\(\d+\)', c) or re.search(r'\b\d+\b', c):
            return c
    return candidates[0]

def parse_decision(text, url):
    # Normalize: strip markdown bold, any stray HTML tags, nbsp; collapse runs.
    t = text.replace("\u00a0", " ")
    t = re.sub(r'</?(strong|p|br|em|span|div)\s*/?>', ' ', t, flags=re.I)
    t = re.sub(r'\*\*', '', t)
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    flat = re.sub(r'\s+', ' ', " ".join(lines))

    def find(pat, default=""):
        m = re.search(pat, flat, re.I)
        return m.group(1).strip() if m else default

    # Address from the Location label, bounded so it can't swallow later fields.
    address = find(r'Location:\s*(.+?)\s+(?:General Description|Reason For|Tree\(?s?\)?|Number of|Application|$)')
    if not address:
        m = re.search(r'INTENDED DECISION:\s*([A-Z0-9 ]+?)(?:\s{2,}|This is|Date|$)', flat)
        address = m.group(1).strip() if m else url.rstrip("/").split("/")[-1]

    issued  = find(r'Date Issued:\s*([0-9/]+)') or find(r'issued\s+([0-9/]{8,10})')
    appeal  = find(r'Appeals Must Be Received By:\s*([0-9/]+)')
    appno   = find(r'Application Number:?\s*([A-Za-z0-9\-]+)')

    # Label-bounded value capture: grab text after a label up to the NEXT label.
    # This is what fixes removals reading 0 and counts landing in wrong columns.
    NEXT = (r'(?=\s*(?:Tree\s*\(?s?\)?\s+(?:to\s+be|that\s+will\s+be)\s+'
            r'(?:Removed|Relocated|Pruned|Transplanted)|'
            r'Number of Replacement|Replacement Tree|Reason For|'
            r'General Description|Trees listed above|Contact|$))')

    def field_value(action):
        # action e.g. r'Removed', r'Relocated', r'Pruned'
        # Tolerates "Tree(s) To Be Removed & Location:", "Tree (s) to be removed
        # and locations (s):", extra spaces, and varied casing.
        pat = (r'Tree\s*\(?s?\)?\s+(?:to\s+be|that\s+will\s+be)\s+' + action +
               r'[^:]*:\s*(.*?)' + NEXT)
        m = re.search(pat, flat, re.I)
        return m.group(1).strip(" .") if m else ""

    remove_txt   = field_value(r'Removed')
    relocate_txt = field_value(r'(?:Relocated|Transplanted)')
    prune_txt    = field_value(r'Pruned')

    m_repl = re.search(r'(?:Number of Replacement Trees?|Replacement Trees?)[^:]*:\s*(.*?)'
                       + NEXT, flat, re.I)
    replace_txt = m_repl.group(1).strip(" .") if m_repl else ""

    n_remove   = count_in_phrase(remove_txt)
    n_relocate = count_in_phrase(relocate_txt)

    # Specimen / prohibited: only a number if the page actually uses the word.
    def explicit_count(keyword, scope_txt):
        if not scope_txt and keyword not in flat.lower():
            return None
        for m in re.finditer(r'(\(\d+\)|\b\w+\b)\s+' + keyword, flat, re.I):
            v = count_in_phrase(m.group(0))
            if v: return v
        return None

    specimen_remove   = explicit_count("specimen", remove_txt)
    specimen_relocate = explicit_count("specimen", relocate_txt)
    prohibited        = explicit_count("prohibited", flat)

    base = n_remove + n_relocate
    spec_known = (specimen_remove or 0) + (specimen_relocate or 0)
    nonspec = base - spec_known if base else 0

    repl_full = replace_txt or "—"

    return {
        "address": address,
        "issued": issued,
        "appeal": appeal,
        "appno": appno,
        "url": url,
        "n_remove": n_remove,
        "n_relocate": n_relocate,
        "specimen_remove": specimen_remove,
        "specimen_relocate": specimen_relocate,
        "nonspec": nonspec,
        "prohibited": prohibited,
        "replacements": repl_full,
        "remove_txt": remove_txt or "—",
        "relocate_txt": relocate_txt or "—",
        "prune_txt": prune_txt or "—",
    }

def get_decision_links(index_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(index_html, "html.parser")
    seen, links = set(), []
    # A real decision page lives under the tree-permitting section and its final
    # path segment starts with "INTENDED-DECISION" followed by an address.
    # This excludes the glossary ("/Glossary/Intended-Decision"), the
    # "Appeal-an-Intended-Decision-Trees" help page, and the listing page itself.
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        full = href if href.startswith("http") else BASE + href
        if "View-Intended-Decisions-Posted-for-Tree-Permitting/" not in full:
            continue
        slug = full.rstrip("/").split("/")[-1]
        if not slug.upper().startswith("INTENDED-DECISION"):
            continue
        # require something after the INTENDED-DECISION prefix (an address)
        tail = re.sub(r'^INTENDED-?DECISION-?', '', slug, flags=re.I)
        if len(tail) < 3:
            continue
        if full not in seen:
            seen.add(full); links.append(full)
    return links

# ---------------------------------------------------------------------------
# Scrape orchestration
# ---------------------------------------------------------------------------
def date_key(d):
    try:
        return datetime.datetime.strptime(d, "%m/%d/%Y")
    except Exception:
        return datetime.datetime.max  # unknown dates sort last

def scrape():
    index_html = fetch(INDEX_URL)
    links = get_decision_links(index_html)
    rows = []
    for url in links:
        try:
            rows.append(parse_decision(fetch(url), url))
        except Exception as e:
            rows.append({"address": url.split("/")[-1], "issued":"", "appeal":"",
                         "appno":"", "url":url, "n_remove":0, "n_relocate":0,
                         "specimen_remove":None,"specimen_relocate":None,"nonspec":0,
                         "prohibited":None,"replacements":f"(parse error: {e})",
                         "remove_txt":"","relocate_txt":"","prune_txt":""})
    # sort by date posted (issued), newest first
    rows.sort(key=lambda r: date_key(r["issued"]), reverse=True)
    return rows

# ---------------------------------------------------------------------------
# Highlight tiers by absolute tree count (removals + relocations):
#   total >= 15  -> red
#   8 <= total <= 14 -> yellow
#   else -> no highlight
# Counts that are non-numeric (e.g. "pending scrape") are treated as 0.
# ---------------------------------------------------------------------------
RED_THRESHOLD = 15
YELLOW_THRESHOLD = 8

def assign_tiers(rows):
    def total_trees(r):
        rm = r["n_remove"] if isinstance(r["n_remove"], int) else 0
        rl = r["n_relocate"] if isinstance(r["n_relocate"], int) else 0
        return rm + rl
    for r in rows:
        t = total_trees(r)
        if t >= RED_THRESHOLD:
            r["tier"] = "red"
        elif t >= YELLOW_THRESHOLD:
            r["tier"] = "yellow"
        else:
            r["tier"] = ""
    return rows

# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------
def cell(v):
    return "not stated" if v is None else html.escape(str(v))

def render(rows):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = []
    for r in rows:
        spec_total = (r["specimen_remove"] or 0) + (r["specimen_relocate"] or 0)
        body.append(f"""<tr class="{r['tier']}">
<td class="addr"><a href="{html.escape(r['url'])}" target="_blank">{cell(r['address'])}</a><div class="app">{cell(r['appno'])}</div></td>
<td>{cell(r['issued'])}</td>
<td>{cell(r['appeal'])}</td>
<td class="num">{cell(r['n_remove'])}</td>
<td class="num">{cell(r['n_relocate'])}</td>
<td class="num">{cell(r['specimen_remove'])}</td>
<td class="num">{cell(r['specimen_relocate'])}</td>
<td class="num">{cell(r['nonspec'])}</td>
<td class="num">{cell(r['prohibited'])}</td>
<td class="repl">{cell(r['replacements'])}</td>
</tr>""")
    rows_html = "\n".join(body) if body else '<tr><td colspan="10">No decisions found.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Miami Tree-Removal Intended Decisions</title>
<style>
 :root {{ --red:#fdd; --redb:#c0392b; --yel:#fff6cc; --yelb:#caa307; }}
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; background:#f5f6f7; color:#1d1f21; }}
 header {{ background:#0b5e3b; color:#fff; padding:18px 22px; }}
 header h1 {{ margin:0; font-size:1.25rem; }}
 .bar {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; padding:12px 22px; background:#fff; border-bottom:1px solid #e2e4e6; }}
 button {{ background:#0b5e3b; color:#fff; border:0; padding:9px 16px; border-radius:6px; font-size:0.95rem; cursor:pointer; }}
 button:hover {{ background:#0d7049; }}
 .meta {{ font-size:0.82rem; color:#555; }}
 .legend span {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.78rem; margin-right:6px; }}
 .lg-red {{ background:var(--red); border:1px solid var(--redb); }}
 .lg-yel {{ background:var(--yel); border:1px solid var(--yelb); }}
 .wrap {{ overflow-x:auto; padding:0 22px 40px; }}
 table {{ border-collapse:collapse; width:100%; background:#fff; font-size:0.86rem; min-width:1000px; }}
 th, td {{ border:1px solid #e2e4e6; padding:8px 10px; text-align:left; vertical-align:top; }}
 th {{ background:#eef1f0; position:sticky; top:0; font-size:0.78rem; }}
 td.num {{ text-align:center; }}
 td.addr a {{ color:#0b5e3b; font-weight:600; text-decoration:none; }}
 td.app {{ font-size:0.72rem; color:#888; }}
 td.repl {{ max-width:320px; font-size:0.8rem; }}
 tr.red {{ background:var(--red); }}
 tr.red td:first-child {{ border-left:4px solid var(--redb); }}
 tr.yellow {{ background:var(--yel); }}
 tr.yellow td:first-child {{ border-left:4px solid var(--yelb); }}
 .note {{ padding:10px 22px; font-size:0.8rem; color:#666; }}
 #spin {{ display:none; }}
</style></head>
<body>
<header><h1>🌳 Miami Tree-Removal Intended Decisions</h1></header>
<div class="bar">
  <button onclick="doRefresh()">↻ Refresh</button>
  <span class="meta">Auto-updated {ts} UTC &nbsp;•&nbsp; {len(rows)} decisions</span>
  <span class="legend"><span class="lg-red">15+ trees</span><span class="lg-yel">8–14 trees</span></span>
</div>
<div class="note">Sorted by date posted (newest first). The data is re-scraped automatically
on a schedule; <b>Refresh</b> loads the latest scraped version (it does not scrape live on
click). Rows marked <b>pending scrape</b> will fill with real counts on the first scheduled
run after you deploy. “Specimen”, “relocation”, and “prohibited” counts show <b>not stated</b>
when the city’s notice doesn’t list them (these pages describe trees in prose and rarely flag
specimen/prohibited status). Click an address to open the original notice.</div>
<div class="wrap">
<table>
<thead><tr>
<th>Address</th><th>Date posted</th><th>Appeal by</th>
<th># removal</th><th># relocation</th>
<th>specimen<br>removal</th><th>specimen<br>relocation</th>
<th>non-specimen /<br>non-prohibited</th><th>prohibited</th>
<th>Replacements</th>
</tr></thead>
<tbody>
{rows_html}
</tbody></table>
</div>
<script>
// On GitHub Pages there is no backend, so Refresh reloads the latest
// auto-scraped page, bypassing the browser cache.
function doRefresh() {{
  location.reload(true);
}}
</script>
</body></html>"""

def main():
    open_browser = "--open" in sys.argv  # default: do NOT open (CI runner is headless)
    print("Scraping Miami tree decisions…")
    rows = scrape()
    rows = assign_tiers(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render(rows))
    print(f"Wrote {OUT}  ({len(rows)} decisions)")
    if open_browser:
        try: webbrowser.open("file://" + OUT)
        except Exception: pass

if __name__ == "__main__":
    main()
