# Mini-League HQ

A self-updating website for a Fantasy Premier League mini-league: Manager of the
Month, season top five, the knockout cup, a prize-pot tracker, a hall of shame,
and a monthly round-up formatted to paste straight into an email.

Once it's set up you never touch it. A job on GitHub's servers pulls fresh data
from the official FPL API twice a day and redeploys the site. You just open the
link.

---

## Setup — about ten minutes, once

You need a free GitHub account. Nothing is installed on your computer.

### 1. Create the repository

1. Go to <https://github.com/new>.
2. Repository name: `fpl-league` (anything is fine). Set it to **Public** —
   GitHub Pages is free on public repos.
3. Leave every other box unticked. Click **Create repository**.

### 2. Upload these files

On the empty repository page, click **uploading an existing file**, then drag in
the whole contents of this folder. Keep the folder structure intact — GitHub
preserves it when you drag folders in. Click **Commit changes**.

Your repository should look like this:

```
config.json
README.md
scripts/
  build.py
  fpl_client.py
  make_mock.py
  preview.py
docs/
  index.html
  commentary.json
.github/workflows/
  update.yml
```

> If the `.github` folder doesn't upload (some browsers hide dotted folders),
> create it by hand: **Add file → Create new file**, type
> `.github/workflows/update.yml` as the name, and paste the contents in.

### 3. Turn on Pages

**Settings → Pages → Build and deployment.** Set **Source** to *Deploy from a
branch*, then **Branch: `main`** and **Folder: `/docs`**. Click **Save**.

GitHub publishes the `docs/` folder straight off the branch. There is no deploy
job and no `github-pages` environment, so there is nothing that can queue up and
stall.

### 4. Let it run

**Actions** tab → **Refresh league data** → **Run workflow**. Give it a minute.
When it goes green, your site is live at:

```
https://<your-username>.github.io/fpl-league/
```

Bookmark that. It's the only link you or the league need.

---

## The one thing worth configuring

`config.json`, edited directly on GitHub (click the file, then the pencil icon):

```json
"prize_pot": {
  "currency": "£",
  "entry_fee": 20,
  "manager_of_the_month": 25,
  "cup_winner": 60,
  "cup_runner_up": 30,
  "season": { "1st": 120, "2nd": 60, "3rd": 40, "4th": 25, "5th": 15 }
}
```

Anything left at `0` shows as **TBC** on the site, so you can launch before the
pot is agreed and fill it in later. Saving the file rebuilds the site
automatically.

`motm_basis` decides whether Manager of the Month counts points **net** of
transfer hits (the default, and how the league table itself works) or **gross**.
Change it to `"gross"` if your league would rather ignore hits.

---

## How the monthly split works

This is the fiddly bit, and it's handled automatically.

A gameweek is never split across two months. Each one is assigned, whole, to the
month in which **most of its fixtures are played**. That single rule produces
the right answer in the awkward cases:

| Month | Gameweeks | Why |
|---|---|---|
| August | GW1–2 | Straightforward |
| September | GW3–5 | GW5 finishes Sunday 20 September |
| October | GW6–9 | GW9 spills into 1–2 November, but most of it is played on 31 October, so the whole gameweek counts for October |

The site shows each month's gameweeks and its publication date on the Manager of
the Month tab, so you can always check the split at a glance. A month is marked
**final** only once FPL has confirmed bonus points and auto-substitutions for
every gameweek in it — which is what stops a table changing under you after
you've emailed it out.

If FPL ever publishes a split that disagrees, override it in `config.json`:

```json
"month_overrides": { "2026-10": [6, 7, 8, 9] }
```

---

## Sending the cup update each week

Once the cup starts (around GW34) it runs one knockout round per gameweek, so
those updates are weekly, not monthly. The **Cup** tab has its own bulletin
underneath the bracket, with a round picker and a **Copy this round** button —
same copy-and-paste flow as the monthly round-up, just shorter.

Each bulletin is written from the results: the biggest thrashing, the tie decided
by a point or two, and — the good bit — any manager who knocked out someone above
them in the league table. A round only appears once FPL has finalised that
gameweek, so the scores can't move after you've sent it.

---

## Sending the monthly email

Open the site → **Email round-up** tab → **Copy formatted round-up** → paste into
Gmail or Outlook. Tables, headings and formatting come across intact. There's a
plain-text button too, and **Print / save as PDF** if you'd rather attach it.

The round-up starts as an auto-generated version built from the numbers. A
hand-written edition lands in `docs/commentary.json` each month and replaces it
on the site automatically.

---

## Running it locally (optional)

```bash
python3 scripts/build.py          # writes docs/data.json from the live API
python3 -m http.server -d docs    # then open http://localhost:8000
```

Testing without touching the network:

```bash
python3 scripts/make_mock.py --out /tmp/mock --gws 9
python3 scripts/build.py --offline /tmp/mock --out /tmp/out.json
python3 scripts/preview.py /tmp/out.json /tmp/preview.html   # single-file preview
```

---

## Notes

- Everything comes from the official public FPL API. No login, no API key, no
  scraping, and nothing that can get an account flagged.
- Requests are cached: a finished gameweek is fetched once and never again, so a
  typical refresh is a few dozen requests rather than several hundred.
- The cup reads from FPL's own mini-league cup once the draw is made (usually
  around GW34). Until then the Cup tab says so. FPL has moved that endpoint
  between seasons, so the code tries the known variants and falls back to
  assembling the bracket from each manager's own cup feed.
- No cookies, no local storage, no tracking. The site is one HTML file and one
  JSON file.
