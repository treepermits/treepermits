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
    Handles comma-separated AND space-only-separated items like:
    "ONE (1) Live Oak (specimen) ONE (1) Mango within the property"
    """
    if not phrase:
        return 0, 0

    # Normalise: insert a sentinel | before each new item boundary.
    # A new item starts when a word-number + (N) pattern follows a non-start position.
    # Use (?<=[\w)]) to also catch boundaries after closing parens like "(specimen)".
    word_nums = "|".join(WORDNUM.keys())
    normalised = re.sub(
        r'(?<=[\w)])\s+(?=(?:' + word_nums + r')\s*\(\d+\)|(?:' + word_nums + r')\s+\d)',
        '|', phrase, flags=re.I)
    # Also split on commas/semicolons or contextual separators like "neighbors".
    normalised = re.sub(r'[,;]|\bneighbors?\b', '|', normalised, flags=re.I)
    items = [i.strip() for i in normalised.split('|') if i.strip()]

    palms = 0
    trees = 0
    for item in items:
        # Extract count: prefer parenthetical (N).
        cnt_m = re.search(r'\((\d+)\)', item)
        if cnt_m:
            cnt = int(cnt_m.group(1))
        else:
            # Try leading word-number.
            cnt_m2 = re.match(r'^\s*(' + word_nums + r')\b', item, re.I)
            cnt = WORDNUM.get(cnt_m2.group(1).lower(), 1) if cnt_m2 else 1

        # Species: everything after the count expression, strip location/condition words.
        species = re.sub(r'\(?\d+\)?', '', item)
        species = re.sub(
            r'\b(?:located|within|throughout|at|in|near|along|on|the|right|of|way|'
            r'lot|property|construction|footprint|building|envelope|site|'
            r'poor|condition|adjacent|nearby)\b.*', '',
            species, flags=re.I)
        species = re.sub(r'[^A-Za-z\s]', ' ', species).strip()
        # Remove word-number tokens from species name.
        species = re.sub(r'\b(?:' + word_nums + r')\b', '', species, flags=re.I).strip()

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
        elif trl >= 5 or pr >= 7 or (tr + trl + pr + r["palms_relocate"]) > 4 or (r["specimen_remove"] or 0) + (r["specimen_relocate"] or 0) > 0:
            r["tier"] = "yellow"
        else:
            r["tier"] = ""
    return rows

# ---------------------------------------------------------------------------
# JSON store — persistent record of every decision ever seen
# ---------------------------------------------------------------------------
JSON_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "decisions.json")

def load_db():
    """Load existing decisions from JSON. Returns dict keyed by url."""
    try:
        with open(JSON_OUT, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {r["url"]: r for r in data}
    except Exception:
        return {}

def merge_db(db, new_rows):
    """Merge freshly scraped rows into the database.
    - New decisions are added.
    - Existing decisions are updated (in case of re-scrape with better data).
    - Old decisions are NEVER deleted.
    Returns sorted list of all decisions."""
    for r in new_rows:
        if r["url"] not in db or not db[r["url"]].get("issued"):
            db[r["url"]] = r
        else:
            # Update fields that may have changed, but keep existing data.
            existing = db[r["url"]]
            for k, v in r.items():
                if v and v != "—" and v is not None:
                    existing[k] = v
    all_rows = list(db.values())
    all_rows.sort(key=lambda r: date_key(r.get("issued","")), reverse=True)
    return all_rows

def save_db(all_rows):
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Render — static HTML shell; all data loaded from decisions.json by JS
# ---------------------------------------------------------------------------
def render(ts, n_total):
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Miami Tree Removal Intended Decisions</title>
<style>
 :root{{--red:#fdd;--redb:#c0392b;--org:#ffe5cc;--orgb:#d35400;--yel:#fff6cc;--yelb:#caa307;}}
 *{{box-sizing:border-box}}
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f7;color:#1d1f21}}
 header{{background:#0b5e3b;color:#fff;padding:16px 22px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}
 header h1{{margin:0;font-size:1.2rem}}
 .appeal-btn{{background:#fff;color:#0b5e3b;border:0;padding:9px 16px;border-radius:6px;font-size:.85rem;cursor:pointer;font-weight:600;white-space:nowrap}}
 .appeal-btn:hover{{background:#e8f5e9}}
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
 .wrap{{overflow-x:auto;overflow-y:auto;max-height:calc(100vh - 160px);padding:0 22px 40px}}
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
 #loading{{padding:40px;text-align:center;color:#888}}
 /* Appeal instructions page */
 .appeal-page{{max-width:780px;margin:0 auto;padding:30px 22px 60px}}
 .appeal-page h2{{color:#0b5e3b;font-size:1.3rem;margin:0 0 6px}}
 .appeal-page .intro{{background:#e8f5e9;border-left:4px solid #0b5e3b;padding:14px 16px;border-radius:4px;margin-bottom:28px;font-size:.95rem;line-height:1.6}}
 .appeal-page .intro a{{color:#0b5e3b;font-weight:600}}
 .step{{background:#fff;border:1px solid #e2e4e6;border-radius:8px;margin-bottom:18px;overflow:hidden}}
 .step-header{{padding:14px 18px;cursor:default;display:flex;align-items:flex-start;gap:12px}}
 .step-num{{background:#0b5e3b;color:#fff;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:.85rem;font-weight:700;flex-shrink:0;margin-top:1px}}
 .step-title{{font-weight:600;font-size:.95rem;color:#1d1f21;line-height:1.4}}
 .step-body{{padding:0 18px 16px 58px;font-size:.88rem;line-height:1.65;color:#333}}
 .step-body ul{{margin:6px 0;padding-left:18px}}
 .step-body li{{margin-bottom:4px}}
 .step-body a{{color:#0b5e3b}}
 .step-body .email-template{{background:#f5f6f7;border:1px solid #e2e4e6;border-radius:4px;padding:10px 14px;font-family:monospace;font-size:.82rem;line-height:1.6;margin:10px 0}}
 .step-body strong{{color:#1d1f21}}
 .collapsible-trigger{{color:#0b5e3b;cursor:pointer;text-decoration:underline;font-weight:600;font-size:.88rem}}
 .collapsible-content{{display:none;margin-top:10px;border-top:1px solid #e2e4e6;padding-top:10px}}
 .collapsible-content.open{{display:block}}
 .example-trigger{{color:#0b5e3b;cursor:pointer;text-decoration:underline;font-size:.85rem;display:block;margin:4px 0}}
 .example-body{{display:none;background:#f9f9f9;border-left:3px solid #ccc;padding:8px 12px;margin:4px 0 8px;font-size:.84rem;line-height:1.6;border-radius:0 4px 4px 0}}
 .example-body.open{{display:block}}
 table.template-table{{border-collapse:collapse;font-size:.8rem;margin:10px 0;width:100%}}
 table.template-table th,table.template-table td{{border:1px solid #ccc;padding:5px 8px}}
 table.template-table th{{background:#eef1f0}}
</style></head>
<body>
<header>
  <h1>🌳 Miami Tree Removal Intended Decisions</h1>
  <button class="appeal-btn" onclick="switchTab('appeal',this)">❓ How to appeal a tree removal decision?</button>
</header>
<div class="bar">
  <button onclick="location.reload(true)">↻ Refresh</button>
  <span class="meta">Data updated {ts} UTC &nbsp;•&nbsp; {n_total} decisions on record</span>
  <span class="legend">
    <span class="lg-red">tree removal 8+</span>
    <span class="lg-org">tree removal 5–7 or relocation 7+ (w/ removals)</span>
    <span class="lg-yel">relocation 5+ or specimen or palm removal 7+</span>
  </span>
</div>
<div class="gs-banner" id="gs-banner">
  📋 Manual columns load from Google Sheet.
  <span id="gs-status">Connecting…</span>
  &nbsp;|&nbsp; <a href="#" onclick="openSheet()">Open Sheet to edit</a>
</div>
<div class="tabs">
  <div class="tab active" onclick="switchTab('active',this)" id="tab-active">Active decisions</div>
  <div class="tab" onclick="switchTab('expired',this)" id="tab-expired">Expired decisions</div>
</div>

<div id="pane-active" class="pane active">
<div class="note">Appeal deadline has not yet passed. Hover the last column to add a note.</div>
<div class="wrap"><table>
<thead><tr>
<th>Address</th><th>Date posted</th><th>Appeal by</th>
<th># tree<br>removal</th><th># tree<br>relocation</th>
<th># palm<br>removal</th><th># palm<br>relocation</th>
<th>specimen<br>removal</th><th>specimen<br>relocation</th>
<th>prohibited</th><th>Reason</th><th>Replacements</th>
<th>Appeal in<br>the works</th>
</tr></thead>
<tbody id="tbody-active"><tr><td colspan="13" id="loading">Loading…</td></tr></tbody>
</table></div></div>

<div id="pane-expired" class="pane">
<div class="note">Appeal deadline has passed. Rows stay here permanently.</div>
<div class="wrap"><table>
<thead><tr>
<th>Address</th><th>Date posted</th><th>Appeal by</th>
<th># tree<br>removal</th><th># tree<br>relocation</th>
<th># palm<br>removal</th><th># palm<br>relocation</th>
<th>specimen<br>removal</th><th>specimen<br>relocation</th>
<th>prohibited</th><th>Reason</th><th>Replacements</th>
<th>Appeal submitted –<br>date of hearing</th>
</tr></thead>
<tbody id="tbody-expired"></tbody>
</table></div></div>

<div id="pane-appeal" class="pane">
<div class="appeal-page">
<h2>How to appeal a tree removal decision?</h2>
<div class="intro">
  Would you like to challenge a decision to remove trees on one of the properties listed on this site?
  Take a look at the deadline to file an appeal — if it still hasn't passed, you can do it!<br><br>
  If you need help, <strong><a href="https://chat.whatsapp.com/EZmplvkz2EI8CZbU2pZWnj?mode=gi_t" target="_blank">join our WhatsApp chat</a></strong> — we can help you file the appeal.
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">1</div>
    <div class="step-title">Figure out who can file the appeal</div>
  </div>
  <div class="step-body">
    <ul>
      <li><strong>Are you the next-door neighbor?</strong> Great — you'll pay a reduced fee to file.</li>
      <li><strong>Not next-door?</strong> Can you find the next-door neighbor who'd like to file and appear at City Hall when called?</li>
      <li><strong>You live within 500ft and there's an HOA?</strong> The HOA can file for a reduced fee.</li>
      <li><strong>No HOA but within 500ft?</strong> You can still apply, but you'll pay an extra $200.</li>
      <li><strong>None of the above?</strong> Text us in the <a href="https://chat.whatsapp.com/EZmplvkz2EI8CZbU2pZWnj?mode=gi_t" target="_blank">chat</a> — we might be able to help you submit the appeal without the extra $200.</li>
    </ul>
  </div>
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">2</div>
    <div class="step-title">Request the invoice by email</div>
  </div>
  <div class="step-body">
    Locate the <strong>Decision Application Number</strong> (looks like <code>BD25-000000-001</code>) and the address from the table above. Then send an email to <a href="mailto:PZHearingBoards@miami.gov">PZHearingBoards@miami.gov</a> as soon as possible:
    <div class="email-template">
      <strong>If you are NOT the next-door neighbor:</strong><br>
      My name is (NAME) and I am requesting the invoice for the appeal of the Intended Decision Application No. (NUMBER). The subject property address is (ADDRESS).<br><br>
      <strong>If you ARE the next-door neighbor:</strong><br>
      My name is (NAME) and I am requesting the invoice for the appeal of the Intended Decision Application No. (NUMBER) as an ABUTTING PROPERTY OWNER. The subject property address is (ADDRESS).
    </div>
    You will receive the invoice the same day.
  </div>
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">3</div>
    <div class="step-title">Pay the fees</div>
  </div>
  <div class="step-body">
    There is a number on the invoice you received. Go to <a href="https://www.miami.gov/Permits-Construction/Make-a-Payment-to-the-City-of-Miami" target="_blank">miami.gov/make-a-payment</a>, enter your invoice number, and pay the fees.<br><br>
    <strong>Save the receipt!</strong> You will need to submit it later.
  </div>
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">4</div>
    <div class="step-title">Download the screenshot of radius and the list of residents within 500ft</div>
  </div>
  <div class="step-body">
    <span class="collapsible-trigger" onclick="toggleCollapsible(this)">▶ Show step-by-step instructions</span>
    <div class="collapsible-content">
      <ol>
        <li>Use this link: <a href="https://gis.miami.gov/miamizoning/main" target="_blank">https://gis.miami.gov/miamizoning/main</a><br>Type the address of the property in.</li>
        <li>On the right side choose this icon and type "500" feet in the pop up "Buffer" window.<br>
          <img src="step4-buffer.png" alt="Buffer tool screenshot" style="max-width:100%;margin:8px 0;border-radius:4px;border:1px solid #e2e4e6"></li>
        <li>Take a screenshot of the radius. You will need to upload it with your appeal. Like this:<br>
          <img src="step4-radius.png" alt="500ft radius screenshot" style="max-width:100%;margin:8px 0;border-radius:4px;border:1px solid #e2e4e6"><br>
          When you save the screenshot, name the file "[TODAY'S DATE] – [property address] – 500ft radius".</li>
        <li>Use another icon to download the excel file with mailing addresses of people within the 500ft radius.<br>
          <img src="step4-download.png" alt="Download icon screenshot" style="max-width:100%;margin:8px 0;border-radius:4px;border:1px solid #e2e4e6"></li>
        <li>Copy appropriate columns into this template:
          <table class="template-table" style="font-size:.72rem">
            <thead><tr><th>Owner Name</th><th>Mailing Street Address</th><th>City</th><th>State</th><th>Zip</th><th>Country</th><th>Folio #</th></tr></thead>
            <tbody>
              <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
              <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
            </tbody>
          </table>
          Do not add, re-arrange, or delete any columns. Use only the seven (7) columns shown: 1) Owner Name 2) Mailing Address 3) City 4) State 5) Zip Code 6) Country 7) Folio Number.
        </li>
        <li>Make sure there are no duplicates, no empty lines. Make sure one owner does not have several mailing addresses (only keep one). Also, if you see a long list of addresses with apartment numbers, you must find out the name of the condominium building or co-op, delete them all and replace with ONLY one line for Condominium Association and one mailing address.<br><br>
          The number of lines will determine the final price of your appeal, because you are responsible for paying to notify them all by mail about your appeal.
        </li>
        <li>Save the file in the excel (!) format with date in the name: "[TODAY'S DATE] – [property address] – list of owners 500ft radius"</li>
      </ol>
    </div>
  </div>
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">5</div>
    <div class="step-title">Complete the property owner affirmation form</div>
  </div>
  <div class="step-body">
    Print <a href="https://www.miami.gov/files/assets/public/v/1/hb-property-owner-affirmation-form.pdf" target="_blank">this form</a>.
    Write in pen the number of "property owners shown in the attached Excel file" (the count from your list).
    Sign, scan, and save with the date and property address in the filename.
  </div>
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">6</div>
    <div class="step-title">Write your appeal letter</div>
  </div>
  <div class="step-body">
    Prepare a letter to the city explaining why you think the trees should stay.
    <span class="collapsible-trigger" onclick="toggleCollapsible(this)">▶ Show example letters</span>
    <div class="collapsible-content">
      <span class="example-trigger" onclick="toggleExample(this)">▶ Example 1</span>
      <div class="example-body">Example letter 1 — will be added here.</div>
      <span class="example-trigger" onclick="toggleExample(this)">▶ Example 2</span>
      <div class="example-body">Example letter 2 — will be added here.</div>
      <span class="example-trigger" onclick="toggleExample(this)">▶ Example 3</span>
      <div class="example-body">Example letter 3 — will be added here.</div>
    </div>
    <br>Print the letter, sign in pen, scan, and save with the date and property address in the filename.
  </div>
</div>

<div class="step">
  <div class="step-header">
    <div class="step-num">7</div>
    <div class="step-title">Submit the appeal online</div>
  </div>
  <div class="step-body">
    Submit using <a href="https://us.openforms.com/Form/f7edcf40-ed3c-4509-b5d7-2be5761ec06f" target="_blank">this online form</a>.
    The first page asks for the date — use the date the intended decision was posted.<br><br>
    Upload all of the following:
    <ul>
      <li>Receipt of paid invoice</li>
      <li>Screenshot of 500ft radius</li>
      <li>Excel file with the list of owners within 500ft</li>
      <li>Scan of the affirmation form (stating how many owners)</li>
      <li>Scan of your signed appeal letter</li>
      <li>Any other supporting files (optional): photos of trees, nests, site plans, arborist reports, anything you deem relevant</li>
    </ul>
    That's it! Await the city's confirmation that your submission is timely and complete. 🌳
  </div>
</div>

</div>
</div>

<script>
// ── Helpers ────────────────────────────────────────────────────────────────
function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
function ns(v) {{
  return (v===null||v===undefined||v==='')?'not stated':esc(String(v));
}}
function dash(v) {{
  return (v===null||v===undefined||v===''||v==='—')?'—':esc(String(v));
}}

// ── Tier logic ─────────────────────────────────────────────────────────────
function tierFor(r) {{
  const tr  = parseInt(r.trees_remove)   || 0;
  const trl = parseInt(r.trees_relocate) || 0;
  const pr  = parseInt(r.palms_remove)   || 0;
  const prl = parseInt(r.palms_relocate) || 0;
  const sr  = parseInt(r.specimen_remove)   || 0;
  const srl = parseInt(r.specimen_relocate) || 0;
  if (tr >= 8) return 'red';
  if ((tr >= 5 && tr <= 7) || (trl >= 7 && tr > 0)) return 'orange';
  if (trl >= 5 || pr >= 7 || (tr+trl+pr+prl) > 4 || (sr+srl) > 0) return 'yellow';
  return '';
}}

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name, el) {{
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('pane-' + name).classList.add('active');
  if (el) el.classList.add('active');
}}

// ── Collapsible sections ───────────────────────────────────────────────────
function toggleCollapsible(trigger) {{
  const content = trigger.nextElementSibling;
  const open = content.classList.toggle('open');
  trigger.textContent = trigger.textContent.replace(open ? '▶' : '▼', open ? '▼' : '▶');
}}

function toggleExample(trigger) {{
  const body = trigger.nextElementSibling;
  const open = body.classList.toggle('open');
  trigger.textContent = trigger.textContent.replace(open ? '▶' : '▼', open ? '▼' : '▶');
}}

// ── Google Sheet ───────────────────────────────────────────────────────────
var SHEET_CSV_URL  = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRzvK7YskKFgC5dOnk8aAjqPNbKL30pCIcIj_-65khI-IUe88v6FDZpeWhnDRuYTW9Dvwf_EzHP1xzH/pub?gid=0&single=true&output=csv";
var SHEET_EDIT_URL = "https://docs.google.com/spreadsheets/d/1FbDfJThEt1Sm_aIli0Lks_i3u2KM2n_CZT9PVHlwXXs/edit?gid=0#gid=0";
var sheetData  = {{}};
var manualRows = [];
var allDecisions = [];

function openSheet() {{ window.open(SHEET_EDIT_URL, '_blank'); }}

function parseCSVLine(line) {{
  const result = []; let cur = '', inQ = false;
  for (let i = 0; i < line.length; i++) {{
    const ch = line[i];
    if (ch === '"') {{ inQ = !inQ; }}
    else if (ch === ',' && !inQ) {{ result.push(cur.trim()); cur = ''; }}
    else {{ cur += ch; }}
  }}
  result.push(cur.trim());
  return result;
}}

function parseCSV(text) {{
  const lines = text.trim().split('\\n');
  for (let i = 1; i < lines.length; i++) {{
    const c = parseCSVLine(lines[i]);
    const appno = (c[0]||'').trim();
    if (!appno) continue;
    sheetData[appno] = {{
      appeal_wip:     c[1]||'',
      hearing:        c[2]||'',
      address:        c[3]||'',
      issued:         c[4]||'',
      appeal:         c[5]||'',
      trees_remove:   c[6]||'',
      trees_relocate: c[7]||'',
      palms_remove:   c[8]||'',
      palms_relocate: c[9]||'',
      spec_remove:    c[10]||'',
      spec_relocate:  c[11]||'',
      prohibited:     c[12]||'',
      reason:         c[13]||'',
      replacements:   c[14]||'',
    }};
  }}
}}

async function loadSheet() {{
  try {{
    const r = await fetch(SHEET_CSV_URL + '&t=' + Date.now());
    const text = await r.text();
    parseCSV(text);
    // Collect manual-only rows (address in Sheet but not in decisions.json).
    const jsonAppnos = new Set(allDecisions.map(d => d.appno).filter(Boolean));
    manualRows = Object.entries(sheetData)
      .filter(([k,v]) => v.address && !jsonAppnos.has(k))
      .map(([k,v]) => ({{appno:k,...v}}));
    document.getElementById('gs-status').textContent =
      '✓ Loaded' + (manualRows.length ? ` (+${{manualRows.length}} manual rows)` : '');
  }} catch(e) {{
    document.getElementById('gs-status').textContent = '⚠️ Could not load Sheet: ' + e;
  }}
}}

// ── Build a table row HTML string ──────────────────────────────────────────
function buildRow(r, isExpired) {{
  const tier = tierFor(r);
  const appno = esc(r.appno||'');
  const wip   = sheetData[r.appno]?.appeal_wip || '';
  const hrg   = sheetData[r.appno]?.hearing    || '';
  const lastCol = isExpired
    ? `<td class="manual" data-field="hearing" data-key="${{appno}}"><span class="val">${{esc(hrg)}}</span><input type="text" placeholder="add date…" value="${{esc(hrg)}}" onchange="saveField(this,'hearing','${{appno}}')"></td>`
    : `<td class="manual" data-field="appeal_wip" data-key="${{appno}}"><span class="val">${{esc(wip)}}</span><input type="text" placeholder="add note…" value="${{esc(wip)}}" onchange="saveField(this,'appeal_wip','${{appno}}')"></td>`;
  const addrCell = r.url
    ? `<a href="${{esc(r.url)}}" target="_blank">${{esc(r.address)}}</a>`
    : `<span style="font-weight:600;color:#0b5e3b">${{esc(r.address)}}</span>`;
  return `<tr class="${{tier}}" data-appno="${{appno}}">
<td class="addr">${{addrCell}}<div class="app">${{esc(r.appno)}}</div></td>
<td>${{dash(r.issued)}}</td><td>${{dash(r.appeal)}}</td>
<td class="num">${{ns(r.trees_remove)}}</td>
<td class="num">${{ns(r.trees_relocate)}}</td>
<td class="num">${{ns(r.palms_remove)}}</td>
<td class="num">${{ns(r.palms_relocate)}}</td>
<td class="num">${{ns(r.specimen_remove)}}</td>
<td class="num">${{ns(r.specimen_relocate)}}</td>
<td class="num">${{ns(r.prohibited)}}</td>
<td class="reason">${{dash(r.reason)}}</td>
<td class="repl">${{dash(r.replacements)}}</td>
${{lastCol}}
</tr>`;
}}

// ── Main render: split by date, inject manual rows, update counts ──────────
function renderTables() {{
  const today = new Date(); today.setHours(0,0,0,0);
  function toDate(s) {{
    if (!s) return null;
    const p = s.split('/');
    return p.length===3 ? new Date(p[2],p[0]-1,p[1]) : null;
  }}

  const active = [], expired = [];
  allDecisions.forEach(r => {{
    const d = toDate(r.appeal);
    (d && d < today ? expired : active).push(r);
  }});

  // Add manual-only rows to expired.
  manualRows.forEach(d => {{
    expired.push({{
      address: d.address, issued: d.issued, appeal: d.appeal,
      appno: d.appno, url: '',
      trees_remove: d.trees_remove, trees_relocate: d.trees_relocate,
      palms_remove: d.palms_remove, palms_relocate: d.palms_relocate,
      specimen_remove: d.spec_remove, specimen_relocate: d.spec_relocate,
      prohibited: d.prohibited, reason: d.reason, replacements: d.replacements,
    }});
  }});
  expired.sort((a,b) => (toDate(b.issued)||new Date(0)) - (toDate(a.issued)||new Date(0)));

  document.getElementById('tbody-active').innerHTML =
    active.length ? active.map(r => buildRow(r,false)).join('') :
    '<tr><td colspan="13" style="text-align:center;color:#888;padding:24px">No active decisions.</td></tr>';
  document.getElementById('tbody-expired').innerHTML =
    expired.length ? expired.map(r => buildRow(r,true)).join('') :
    '<tr><td colspan="13" style="text-align:center;color:#888;padding:24px">No expired decisions yet.</td></tr>';

  document.getElementById('tab-active').textContent  = `Active decisions (${{active.length}})`;
  document.getElementById('tab-expired').textContent = `Expired decisions (${{expired.length}})`;
}}

// ── Save field ─────────────────────────────────────────────────────────────
function saveField(input, field, key) {{
  const val = input.value.trim();
  input.previousElementSibling.textContent = val;
  const label = field==='hearing' ? 'Appeal submitted – date of hearing' : 'Appeal in the works';
  if (confirm(`To save to the shared Sheet:\\n\\nApp#: ${{key}}\\nField: ${{label}}\\nValue: ${{val}}\\n\\nOpen the Sheet now?`))
    window.open(SHEET_EDIT_URL,'_blank');
}}

// ── Boot ───────────────────────────────────────────────────────────────────
async function init() {{
  // Load decisions.json (permanent record).
  try {{
    const r = await fetch('decisions.json?t=' + Date.now());
    allDecisions = await r.json();
  }} catch(e) {{
    document.getElementById('tbody-active').innerHTML =
      `<tr><td colspan="13" style="color:red;padding:24px">Could not load decisions.json: ${{e}}</td></tr>`;
    return;
  }}
  // Load Sheet (manual columns + manual rows).
  await loadSheet();
  // Render everything.
  renderTables();
}}

init();
</script>
</body></html>"""

def main():
    open_browser = "--open" in sys.argv
    print("Scraping Miami tree decisions…")
    new_rows = scrape()
    new_rows = assign_tiers(new_rows)

    # Load existing DB, merge, save.
    db = load_db()
    before = len(db)
    all_rows = merge_db(db, new_rows)
    after = len(db)
    save_db(all_rows)
    print(f"DB: {before} existing + {after-before} new = {after} total decisions")

    # Write static HTML shell.
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render(ts, after))
    print(f"Wrote {OUT}")

    if open_browser:
        try: webbrowser.open("file://" + OUT)
        except Exception: pass

if __name__ == "__main__":
    main()
    ts    = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

