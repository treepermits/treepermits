# Miami Tree-Removal Intended-Decision Monitor

Scrapes the City of Miami **"View Intended Decisions Posted for Tree Permitting"**
page, parses each notice, and produces a sortable, color-coded HTML table with a
working **Refresh** button.

## Files
- `tree_monitor.py` — the scraper + HTML generator (the engine).
- `serve.py` — tiny local server that makes the **Refresh** button do a real re-scrape.
- `tree_decisions.html` — the generated report (open this). Ships pre-populated
  with the current decisions so you can see it working immediately.

## Quick start (Refresh button works)
```bash
pip install requests beautifulsoup4
python3 serve.py
```
Then open **http://localhost:8731**. Clicking **Refresh** re-runs the scraper and
reloads the table.

## Simple start (no server)
```bash
pip install requests beautifulsoup4
python3 tree_monitor.py          # scrapes, writes tree_decisions.html, opens it
```
Here the Refresh button just reloads the file — re-run the command to update data.

## If the city site blocks plain requests (HTTP 403)
Some networks / the city CDN block non-browser traffic. Install the headless
browser fallback once:
```bash
pip install playwright
playwright install chromium
```
The script auto-falls back to it when `requests` is blocked.

## Run it automatically every day (optional)
macOS/Linux — `crontab -e`, add (7am daily):
```
0 7 * * * /usr/bin/python3 /full/path/to/tree_monitor.py --no-open
```
Windows — Task Scheduler → Create Basic Task → Daily → Action:
`python C:\path\to\tree_monitor.py --no-open`

## The table
Columns: Address · Date posted · Appeal-by date · # removal · # relocation ·
specimen removal · specimen relocation · non-specimen/non-prohibited · prohibited ·
Replacements. Sorted by **date posted, newest first**.

**Highlighting:** the decision(s) with the highest (total trees, then specimen
count) are **red**; the next tier is **yellow**; the rest are unhighlighted.

## Important data caveat
The city's notices describe trees in **prose** and usually **do not label** which
trees are *specimen* or *prohibited* — they only state that trees not flagged as
specimen are non-specimen. So those columns show **"not stated"** unless a notice
explicitly uses those words. "Most egregious" is therefore ranked on the counts
that are reliably extractable (total removals + relocations, and specimen when
named). Always click through to the original notice before acting on an appeal.
