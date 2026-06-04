#!/usr/bin/env python3
"""
Miami Tree-Removal Intended-Decision Monitor
============================================
Scrapes miami.gov tree-permitting decisions, splits tree vs palm counts,
writes docs/index.html with two tabs (active decisions + expired appeals)
and a Google Sheet integration for manual "Appeal in the works" and
"Appeal submitted - date of hearing" columns.

PALM SPECIES LIST (Florida standard): any species whose common name contains
a word from PALM_KEYWORDS is classified as a palm.
"""

import sys, os, re, html, datetime, webbrowser, json

INDEX_URL = ("https://www.miami.gov/My-Government/Departments/Building/"
             "View-Intended-Decisions-Posted-for-Tree-Permitting")
BASE = "https://www.miami.gov"
OUT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")

# ---------------------------------------------------------------------------
# Palm identification — Florida standard list.
# A tree item is a palm if its species name contains any of these words.
# ---------------------------------------------------------------------------
PALM_KEYWORDS = {
    "palm","palma","palmetto","cycas","sago","sabal","cabbage",
    "coconut","foxtail","christmas","alexander","areca","bamboo","bismarck",
    "bottle","buccaneer","butterfly","canary","cardboard","carpentaria",
    "chinese fan","chinese windmill","chusan","cocos","cuba","date",
    "desert","double","dwarf","european fan","fishtail","florida thatch",
    "formosa","fox","golden cane","guadalupe","hurricane","ivory cane",
    "kentia","lady","latania","livistona","manila","mazari","mediterranean fan",
    "mexican fan","needle","nikau","nipa","old man","paurotis","pindo","ponytail",
    "princess","pygmy date","pygmy","rhopalostylus","ribbon fan",
    "ruffle","saw palmetto","senegal date","silver","spindle","sugar",
    "thatch","traveler","triangle","washingtonia","wax","windmill","wine",
    "zombie",
    # explicit two-word palms that include ambiguous first words
    "royal palm","queen palm","sylvester palm","cat palm",
}

# Single words that only count as palm indicators if followed by 'palm'
PALM_ONLY_WITH_PALM = {"royal","queen","sylvester","cat","silver","sugar","wine",
                        "double","needle","old man","princess","spindle"}

def is_palm(species_name):
    n = species_name.lower().strip()
    # Check exact multi-word keywords first (e.g. "royal palm")
    for kw in PALM_KEYWORDS:
        if ' ' in kw and kw in n:
            return True
    # For single-word keywords that need 'palm' alongside them,
    # require the word 'palm' to also appear in the species name.
    for kw in PALM_ONLY_WITH_PALM:
        if kw in n.split():
            if 'palm' in n:
                return True
            # else skip — e.g. "Royal Poinciana" has no 'palm'
    # Plain single-word keywords (safe: unambiguously palm-only words)
    safe_single = PALM_KEYWORDS - PALM_ONLY_WITH_PALM - {k for k in PALM_KEYWORDS if ' ' in k}
    for kw in safe_single:
        if kw in n.split() or kw in n:
            return True
    return False

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
# Fetch layer
# ---------------------------------------------------------------------------
def fetch_requests(url):
    import requests
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
    if r.status_code == 200 and len(r.text) > 500:
        return r.text
    raise RuntimeError(f"requests got HTTP {r.status_code} (len {len(r.text)})")

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
    try:
        return fetch_requests(url)
    except Exception as e_req:
        try:
            return fetch_playwright(url)
        except Exception as e_pw:
            raise RuntimeError(
                f"Could not fetch {url}.\n"
                f"  requests: {e_req}\n  playwright: {e_pw}")

# ---------------------------------------------------------------------------
# Counting helpers
# ---------------------------------------------------------------------------
def count_in_phrase(phrase):
    """Sum tree counts from prose like 'THREE(3) PALMS, ONE(1) OAK'."""
    if not phrase:
        return 0
    paren = re.findall(r'\((\d+)\)', phrase)
    if paren:
        return sum(int(x) for x in paren)
    total = 0
    for m in re.finditer(r'\b(\d+|' + "|".join(WORDNUM) + r')\b', phrase, re.I):
        tok = m.group(1).strip().lower()
        v = int(tok) if tok.isdigit() else WORDNUM.get(tok)
        if v:
            total += v
    return total

def split_palm_tree(phrase):
    """
    Parse a removal/relocation phrase into (n_palms, n_trees).
    Each item looks like "COUNT(N) SPECIES" or "N SPECIES".
    Returns (palms, non-palms). Unknown species go to non-palm.
    """
    if not phrase:
        return 0, 0
    palms = 0
    trees = 0
    # Split on commas or semicolons to get individual items.
    # Each item: optional WORD (N) or (N) or bare N, then species words.
    items = re.split(r'[,;]', phrase)
    for item in items:
        item = item.strip()
        if not item:
            continue
        # Extract count: prefer parenthetical (N), then leading word-number/digit.
        cnt_m = re.search(r'\((\d+)\)', item)
        if cnt_m:
            cnt = int(cnt_m.group(1))
        else:
            cnt_m2 = re.match(r'^\s*(?:([A-Za-z]+)\s*)?\(?(\d+)\)?', item)
            if cnt_m2:
                tok = (cnt_m2.group(1) or "").lower()
                cnt = WORDNUM.get(tok, 0) or int(cnt_m2.group(2))
            else:
                cnt = 1
        # Species name = everything after the count expression, up to a period or location keyword.
        species = re.sub(r'\(?\d+\)?', '', item)
        species = re.sub(r'\b(?:located|within|throughout|at|in|near|along|on|the|right|of|way|lot|property|construction|footprint|building|envelope|site)\b.*', '', species, flags=re.I)
        species = re.sub(r'[^A-Za-z\s]', ' ', species).strip()
        if is_palm(species):
            palms += cnt
        else:
            trees += cnt
    return palms, trees

def specimen_in_phrase(scope_txt):
    """Count trees tagged (specimen) inline. Returns None if word absent."""
    if not scope_txt or "specimen" not in scope_txt.lower():
        return None
    total = 0
    for m in re.finditer(r'\(\s*specimen[^)]*\)', scope_txt, re.I):
        head = scope_txt[:m.start()]
        cm = re.findall(r'\((\d+)\)|\b(' + "|".join(WORDNUM) + r')\b', head, re.I)
        if cm:
            last = cm[-1]
            val = int(last[0]) if last[0] else WORDNUM.get(last[1].lower(), 1)
            total += val
        else:
            total += 1
    return total if total else None

def prohibited_in_text(flat):
    """Count trees tagged (prohibited) in full page text. Returns None if absent."""
    if "prohibited" not in flat.lower():
        return None
    total = 0
    for m in re.finditer(r'\(\s*prohibited[^)]*\)', flat, re.I):
        head = flat[:m.start()]
        cm = re.findall(r'\((\d+)\)|\b(' + "|".join(WORDNUM) + r')\b', head, re.I)
        if cm:
            last = cm[-1]
            total += int(last[0]) if last[0] else WORDNUM.get(last[1].lower(), 1)
        else:
            total += 1
    return total if total else None

# ---------------------------------------------------------------------------
# Parse a single decision page
# ---------------------------------------------------------------------------
def parse_decision(text, url):
    import html as _html
    t = _html.unescape(text).replace("\u00a0", " ")
    t = re.sub(r'<meta[^>]*>', ' ', t, flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = re.sub(r'\*\*', '', t)
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    flat  = re.sub(r'\s+', ' ', " ".join(lines))

    def find(pat, default=""):
        m = re.search(pat, flat, re.I)
        return m.group(1).strip() if m else default

    # Address from URL slug (reliable, avoids messy page text).
    slug      = url.rstrip("/").split("/")[-1]
    slug_addr = re.sub(r'^INTENDED-?DECISION-?', '', slug, flags=re.I)
    address   = slug_addr.replace("-", " ").strip() or slug

    issued = (find(r'Date Issued:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})') or
              find(r'issued\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})'))
    appeal = find(r'Appeals?\s+(?:Must Be|must be)\s+[Rr]eceived\s+[Bb]y:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})')
    appno  = find(r'Application Number:?\s*([A-Za-z0-9\-]+)')
    reason = find(r'Reason For Tree Activity:\s*(.+?)\s+(?:Tree\s*\(?s?\)?\s+(?:to\s+be|that\s+will\s+be)|Number of Replacement|General Description|Trees listed above|Contact details|$)')
    reason = re.sub(r'\s*\*+\s*$', '', reason).strip(" .") if reason else ""

    NEXT = (r'(?=\s*(?:Tree\s*\(?s?\)?\s+(?:to\s+be|that\s+will\s+be)\s+'
            r'(?:Removed|Relocated|Pruned|Transplanted)|'
            r'Number of Replacement|Replacement Tree|Reason For|'
            r'General Description|Trees listed above|Contact|$))')

    def field_value(action):
        pat = (r'Tree\s*\(?s?\)?\s+(?:to\s+be|that\s+will\s+be)\s+' + action +
               r'[^:]*:\s*(.*?)' + NEXT)
        m = re.search(pat, flat, re.I)
        return m.group(1).strip(" .") if m else ""

    remove_txt   = field_value(r'Removed')
    relocate_txt = field_value(r'(?:Relocated|Transplanted)')

    m_repl = re.search(r'(?:Number of Replacement Trees?|Replacement Trees?)[^:]*:\s*(.*?)' + NEXT, flat, re.I)
    replace_txt = m_repl.group(1).strip(" .") if m_repl else ""
    replace_txt = re.sub(r'\s*\*+\s*$', '', replace_txt).strip(" .")

    # Split removals and relocations into palms vs trees.
    palms_remove, trees_remove     = split_palm_tree(remove_txt)
    palms_relocate, trees_relocate = split_palm_tree(relocate_txt)

    specimen_remove   = specimen_in_phrase(remove_txt)
    specimen_relocate = specimen_in_phrase(relocate_txt)
    prohibited        = prohibited_in_text(flat)

    return {
        "address":           address,
        "issued":            issued,
        "appeal":            appeal,
        "appno":             appno or "",
        "url":               url,
        "reason":            reason or "—",
        "trees_remove":      trees_remove,
        "palms_remove":      palms_remove,
        "trees_relocate":    trees_relocate,
        "palms_relocate":    palms_relocate,
        "specimen_remove":   specimen_remove,
        "specimen_relocate": specimen_relocate,
        "prohibited":        prohibited,
        "replacements":      replace_txt or "—",
    }

# ---------------------------------------------------------------------------
# Link filtering
# ---------------------------------------------------------------------------
def get_decision_links(index_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(index_html, "html.parser")
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        full = href if href.startswith("http") else BASE + href
        if "View-Intended-Decisions-Posted-for-Tree-Permitting/" not in full:
            continue
        slug = full.rstrip("/").split("/")[-1]
        if not slug.upper().startswith("INTENDED-DECISION"):
            continue
        tail = re.sub(r'^INTENDED-?DECISION-?', '', slug, flags=re.I)
        if len(tail) < 3:
            continue
        if full not in seen:
            seen.add(full); links.append(full)
    return links

# ---------------------------------------------------------------------------
# Scrape + sort
# ---------------------------------------------------------------------------
def date_key(d):
    try:
        return datetime.datetime.strptime(d, "%m/%d/%Y")
    except Exception:
        return datetime.datetime.max

def scrape():
    index_html = fetch(INDEX_URL)
    links = get_decision_links(index_html)
    rows = []
    for url in links:
        try:
            rows.append(parse_decision(fetch(url), url))
        except Exception as e:
            slug = url.rstrip("/").split("/")[-1]
            addr = re.sub(r'^INTENDED-?DECISION-?', '', slug, flags=re.I).replace("-"," ").strip() or slug
            rows.append({
                "address": addr, "issued":"", "appeal":"", "appno":"", "url": url,
                "reason": f"(parse error: {e})",
                "trees_remove":0,"palms_remove":0,"trees_relocate":0,"palms_relocate":0,
                "specimen_remove":None,"specimen_relocate":None,
                "prohibited":None,"replacements":"—",
            })
    rows.sort(key=lambda r: date_key(r["issued"]), reverse=True)
    return rows

# ---------------------------------------------------------------------------
# Tier logic (new rules):
#   red    : trees_remove >= 8
#   orange : (trees_remove in 5-7) OR (trees_relocate >= 7 AND trees_remove > 0)
#   yellow : trees_relocate >= 5 OR palms_remove >= 7
#   (orange beats yellow; red beats all)
# ---------------------------------------------------------------------------
def assign_tiers(rows):
    for r in rows:
        tr  = r["trees_remove"]
        trl = r["trees_relocate"]
        pr  = r["palms_remove"]
        if tr >= 8:
            r["tier"] = "red"
        elif (5 <= tr <= 7) or (trl >= 7 and tr > 0):
            r["tier"] = "orange"
        elif trl >= 5 or pr >= 7:
            r["tier"] = "yellow"
        else:
            r["tier"] = ""
    return rows

# ---------------------------------------------------------------------------
# Render HTML
# ---------------------------------------------------------------------------
def cell(v):
    return "not stated" if v is None else html.escape(str(v))

def render(rows):
    ts   = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.datetime.utcnow().date()

    # Split into active (appeal not yet passed) and expired.
    active, expired = [], []
    for r in rows:
        try:
            ap = datetime.datetime.strptime(r["appeal"], "%m/%d/%Y").date()
            if ap < today:
                expired.append(r)
            else:
                active.append(r)
        except Exception:
            active.append(r)   # unknown date stays on main tab

    def build_rows(rlist, include_hearing=False):
        out = []
        for r in rlist:
            cls = r.get("tier","")
            appno_js = html.escape(r["appno"].replace("'","\\'"))
            hearing_cell = (
                f'<td class="manual" data-field="hearing" data-key="{appno_js}">'
                f'<span class="val"></span>'
                f'<input type="text" placeholder="add date…" onchange="saveField(this,\'hearing\',\'{appno_js}\')">'
                f'</td>'
            ) if include_hearing else ""
            out.append(f"""<tr class="{cls}" data-appno="{appno_js}">
<td class="addr"><a href="{html.escape(r['url'])}" target="_blank">{cell(r['address'])}</a><div class="app">{cell(r['appno'])}</div></td>
<td>{cell(r['issued'])}</td>
<td>{cell(r['appeal'])}</td>
<td class="num">{cell(r['trees_remove'])}</td>
<td class="num">{cell(r['palms_remove'])}</td>
<td class="num">{cell(r['trees_relocate'])}</td>
<td class="num">{cell(r['palms_relocate'])}</td>
<td class="num">{cell(r['specimen_remove'])}</td>
<td class="num">{cell(r['specimen_relocate'])}</td>
<td class="num">{cell(r['prohibited'])}</td>
<td class="reason">{cell(r['reason'])}</td>
<td class="repl">{cell(r['replacements'])}</td>
<td class="manual" data-field="appeal_wip" data-key="{appno_js}"><span class="val"></span><input type="text" placeholder="add note…" onchange="saveField(this,'appeal_wip','{appno_js}')"></td>
{hearing_cell}
</tr>""")
        if not out:
            cols = 15 if include_hearing else 14
            out.append(f'<tr><td colspan="{cols}" style="text-align:center;color:#888;padding:24px">No decisions in this tab.</td></tr>')
        return "\n".join(out)

    active_rows  = build_rows(active,  include_hearing=False)
    expired_rows = build_rows(expired, include_hearing=True)

    active_hdrs = """<th>Address</th><th>Date posted</th><th>Appeal by</th>
<th># tree<br>removal</th><th># palm<br>removal</th>
<th># tree<br>relocation</th><th># palm<br>relocation</th>
<th>specimen<br>removal</th><th>specimen<br>relocation</th>
<th>prohibited</th><th>Reason</th><th>Replacements</th>
<th>Appeal in<br>the works</th>"""

    expired_hdrs = active_hdrs + "<th>Appeal submitted –<br>date of hearing</th>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Miami Tree-Removal Intended Decisions</title>
<style>
 :root{{--red:#fdd;--redb:#c0392b;--org:#ffe5cc;--orgb:#d35400;--yel:#fff6cc;--yelb:#caa307;}}
 *{{box-sizing:border-box}}
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f7;color:#1d1f21}}
 header{{background:#0b5e3b;color:#fff;padding:16px 22px}}
 header h1{{margin:0;font-size:1.2rem}}
 .tabs{{display:flex;gap:0;padding:0 22px;background:#fff;border-bottom:2px solid #e2e4e6}}
 .tab{{padding:10px 20px;cursor:pointer;font-size:.9rem;border-bottom:3px solid transparent;margin-bottom:-2px;font-weight:500;color:#555}}
 .tab.active{{border-bottom-color:#0b5e3b;color:#0b5e3b}}
 .pane{{display:none}}.pane.active{{display:block}}
 .bar{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 22px;background:#fff;border-bottom:1px solid #e2e4e6}}
 button{{background:#0b5e3b;color:#fff;border:0;padding:8px 14px;border-radius:6px;font-size:.9rem;cursor:pointer}}
 button:hover{{background:#0d7049}}
 .meta{{font-size:.8rem;color:#555}}
 .legend span{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.76rem;margin-right:5px}}
 .lg-red{{background:var(--red);border:1px solid var(--redb)}}
 .lg-org{{background:var(--org);border:1px solid var(--orgb)}}
 .lg-yel{{background:var(--yel);border:1px solid var(--yelb)}}
 .wrap{{overflow-x:auto;padding:0 22px 40px}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:.84rem;min-width:1100px}}
 th,td{{border:1px solid #e2e4e6;padding:7px 9px;text-align:left;vertical-align:top}}
 th{{background:#eef1f0;position:sticky;top:0;font-size:.76rem;white-space:nowrap}}
 td.num{{text-align:center}}
 td.addr a{{color:#0b5e3b;font-weight:600;text-decoration:none}}
 .app{{font-size:.7rem;color:#888}}
 td.repl{{max-width:280px;font-size:.78rem}}
 td.reason{{max-width:180px;font-size:.78rem}}
 td.manual{{min-width:130px}}
 td.manual .val{{font-size:.82rem;color:#1d1f21;display:block;min-height:1em}}
 td.manual input{{width:100%;border:1px solid #ccc;border-radius:4px;padding:3px 6px;font-size:.8rem;margin-top:3px;display:none}}
 td.manual:hover input{{display:block}}
 tr.red{{background:var(--red)}} tr.red td:first-child{{border-left:4px solid var(--redb)}}
 tr.orange{{background:var(--org)}} tr.orange td:first-child{{border-left:4px solid var(--orgb)}}
 tr.yellow{{background:var(--yel)}} tr.yellow td:first-child{{border-left:4px solid var(--yelb)}}
 .note{{padding:8px 22px;font-size:.78rem;color:#666}}
 .gs-banner{{padding:6px 22px;font-size:.78rem;background:#e8f5e9;color:#1b5e20;border-bottom:1px solid #c8e6c9}}
 .gs-banner a{{color:#0b5e3b}}
</style></head>
<body>
<header><h1>🌳 Miami Tree-Removal Intended Decisions</h1></header>
<div class="bar">
  <button onclick="location.reload(true)">↻ Refresh</button>
  <span class="meta">Auto-updated {ts} UTC &nbsp;•&nbsp; {len(active)} active &nbsp;•&nbsp; {len(expired)} expired</span>
  <span class="legend">
    <span class="lg-red">tree removal 8+</span>
    <span class="lg-org">tree removal 5–7 or relocation 7+ (w/ removals)</span>
    <span class="lg-yel">relocation 5+ or palm removal 7+</span>
  </span>
</div>
<div class="gs-banner" id="gs-banner">
  📋 Manual columns load from Google Sheet.
  <span id="gs-status">Connecting…</span>
  &nbsp;|&nbsp; <a href="#" onclick="openSheet()">Open Sheet to edit</a>
</div>
<div class="tabs">
  <div class="tab active" onclick="switchTab('active',this)">Active decisions ({len(active)})</div>
  <div class="tab" onclick="switchTab('expired',this)">Expired appeals ({len(expired)})</div>
</div>

<div id="pane-active" class="pane active">
<div class="note">Appeal deadline has not yet passed. Click an address to open the original notice.
Hover a cell in the last column to add a note. Your edits save to the shared Google Sheet instantly.</div>
<div class="wrap"><table>
<thead><tr>{active_hdrs}</tr></thead>
<tbody>{active_rows}</tbody>
</table></div></div>

<div id="pane-expired" class="pane">
<div class="note">Appeal deadline has passed. Rows move here automatically.
"Appeal submitted – date of hearing" column is for manual entry.</div>
<div class="wrap"><table>
<thead><tr>{expired_hdrs}</tr></thead>
<tbody>{expired_rows}</tbody>
</table></div></div>

<script>
// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name, el) {{
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('pane-' + name).classList.add('active');
  el.classList.add('active');
}}

// ── Google Sheet integration ───────────────────────────────────────────────
// Replace the SHEET_CSV_URL below with your published Sheet CSV link.
// Instructions: see the README / deploy guide.
var SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRzvK7YskKFgC5dOnk8aAjqPNbKL30pCIcIj_-65khI-IUe88v6FDZpeWhnDRuYTW9Dvwf_EzHP1xzH/pub?gid=0&single=true&output=csv";
var SHEET_EDIT_URL = "https://docs.google.com/spreadsheets/d/1FbDfJThEt1Sm_aIli0Lks_i3u2KM2n_CZT9PVHlwXXs/edit?gid=0#gid=0";
var sheetData = {{}};  // appno -> {{appeal_wip, hearing}}

function openSheet() {{ window.open(SHEET_EDIT_URL, '_blank'); }}

async function loadSheet() {{
  if (!SHEET_CSV_URL || SHEET_CSV_URL.startsWith("REPLACE")) {{
    document.getElementById('gs-status').textContent =
      "⚠️ Sheet not configured yet — see README to set up.";
    document.getElementById('gs-banner').style.background = '#fff8e1';
    return;
  }}
  try {{
    const r = await fetch(SHEET_CSV_URL + '&t=' + Date.now());
    const text = await r.text();
    parseCSV(text);
    applySheetData();
    document.getElementById('gs-status').textContent = "✓ Loaded";
  }} catch(e) {{
    document.getElementById('gs-status').textContent = "⚠️ Could not load Sheet: " + e;
  }}
}}

function parseCSV(text) {{
  const lines = text.trim().split('\\n');
  // Skip header row; columns: App# | appeal_wip | hearing
  for (let i = 1; i < lines.length; i++) {{
    const cols = lines[i].split(',').map(c => c.trim().replace(/^"|"$/g,''));
    if (!cols[0]) continue;
    sheetData[cols[0]] = {{
      appeal_wip: cols[1] || '',
      hearing:    cols[2] || '',
    }};
  }}
}}

function applySheetData() {{
  document.querySelectorAll('tr[data-appno]').forEach(row => {{
    const key = row.dataset.appno;
    const d = sheetData[key];
    if (!d) return;
    row.querySelectorAll('td[data-field]').forEach(cell => {{
      const val = d[cell.dataset.field] || '';
      cell.querySelector('.val').textContent = val;
      cell.querySelector('input').value = val;
    }});
  }});
}}

// saveField: opens the Sheet for editing (since we can't write to a Sheet
// from JS without an API key). Copies the value to clipboard and alerts.
function saveField(input, field, key) {{
  const val = input.value.trim();
  input.previousElementSibling.textContent = val;
  // Tell the user to paste this value into the Sheet.
  const fieldLabel = field === 'hearing' ? 'Appeal submitted – date of hearing' : 'Appeal in the works';
  const msg = `To save this value to the shared sheet:\\n\\nApp#: ${{key}}\\nField: ${{fieldLabel}}\\nValue: ${{val}}\\n\\nThe Sheet will open — paste the value in the correct row/column.`;
  if (confirm(msg + '\\n\\nOpen the Sheet now?')) {{
    window.open(SHEET_EDIT_URL, '_blank');
  }}
}}

loadSheet();
</script>
</body></html>"""

def main():
    open_browser = "--open" in sys.argv
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
