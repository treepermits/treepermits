# Publish the Miami Tree-Decision Monitor as a free public website

This turns the monitor into a public web page anyone can visit and Refresh —
hosted free on **GitHub Pages**, with a **GitHub Action** that re-scrapes
miami.gov automatically (default: twice a day). No server to maintain, no cost.

> How "Refresh" works here: GitHub re-scrapes on a schedule and republishes the
> page. A visitor clicking **Refresh** loads the latest published version. It is
> not a live-on-click scrape (a static host can't do that), but the data stays
> current automatically. You can also trigger an immediate re-scrape yourself
> from the **Actions** tab ("Run workflow").

## What's in this folder
```
build_site.py                  the scraper; writes docs/index.html
requirements.txt               Python packages the Action installs
docs/index.html                the published page (pre-filled so it works on day one)
.github/workflows/scrape.yml   the scheduled scrape + deploy automation
```

## One-time setup (about 10 minutes)

### Step 1 — Create a GitHub account
Go to https://github.com and sign up (free) if you don't have one.

### Step 2 — Create a new repository
1. Click the **+** (top right) → **New repository**.
2. Name it, e.g. `miami-tree-monitor`.
3. Set it to **Public** (required for free Pages).
4. Click **Create repository**.

### Step 3 — Upload these files
Easiest (no command line):
1. On the new repo page, click **uploading an existing file**.
2. Drag in **all** the files and folders from this package, keeping the
   structure: `build_site.py`, `requirements.txt`, the `docs` folder (with
   `index.html` inside), and the `.github` folder (with
   `workflows/scrape.yml` inside).
   - If the drag-and-drop box won't accept folders, upload the loose files
     first, then create the folders: click **Add file → Create new file**, type
     `docs/index.html` as the name (the `/` makes the folder) and paste the
     contents; repeat for `.github/workflows/scrape.yml`.
3. Click **Commit changes**.

### Step 4 — Turn on GitHub Pages
1. In the repo, go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
   (That's it — the included workflow handles publishing.)

### Step 5 — Run it once
1. Go to the **Actions** tab.
2. If prompted, click the green button to enable workflows.
3. Click the workflow **"Scrape Miami tree decisions and publish"** →
   **Run workflow** → **Run workflow**.
4. Wait ~2–3 minutes for it to finish (green check).

### Step 6 — Visit your site
Your public URL is:
```
https://<your-username>.github.io/<repository-name>/
```
e.g. `https://janedoe.github.io/miami-tree-monitor/`
(Find the exact link under **Settings → Pages** after the first deploy.)

Share that URL with anyone. They click **Refresh** to load the latest data.

## Changing how often it scrapes
Open `.github/workflows/scrape.yml` and edit the `cron:` lines (times are in
**UTC**). Miami is UTC−4 (EDT) / UTC−5 (EST). Examples:
```
- cron: "0 11 * * *"   # ~7am Miami
- cron: "0 23 * * *"   # ~7pm Miami
- cron: "0 11,15,19,23 * * *"   # every 4 hours during the day
```
GitHub may delay scheduled runs by a few minutes at busy times — that's normal.

## If a scrape ever fails
- Open the **Actions** tab and click the failed run to see the log.
- The most common cause is the city site blocking the scraper. The workflow
  already installs a headless browser (Playwright) as a fallback, which handles
  the usual 403 block. If the city changes its page layout, the parser in
  `build_site.py` may need a small tweak.

## Data caveat (unchanged)
The city's notices describe trees in prose and don't flag specimen / prohibited
status, so those columns show **not stated** unless a notice uses those words.
Ranking ("most egregious") uses the reliably extractable counts (total removals
+ relocations, plus specimen when named). Always open the original notice before
acting on an appeal deadline.
