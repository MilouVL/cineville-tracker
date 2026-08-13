# Cineville Amsterdam — Weekly Program Tracker

Automatically builds a webpage every Tuesday around 12:00 (Europe/Amsterdam)
showing the new film program for 14 Cineville-affiliated Amsterdam cinemas,
ordered by proximity to postcode 1075 TR, with new releases highlighted and
an "announced for the next 3 weeks" section.

**Cinemas covered:** Rialto VU, LAB111, Filmhallen, Rialto De Pijp,
Cinecenter, Melkweg Cinema, Cinema De Balie, De Uitkijk, Filmhuis Cavia,
Het Ketelhuis, Kriterion, The Movies, EYE Filmmuseum, Studio/K.

**Data source:** [filmladder.nl](https://www.filmladder.nl) — Cineville
doesn't publish a public API, and filmladder.nl is the most consistent single
source that lists showtimes for all of these venues in one place, plus a
"new this week" and "coming soon" list.

## One-time setup

1. **Create a new GitHub repository** (public or private both work) and push
   everything in this folder to it:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```

2. **Enable GitHub Pages:**
   Repo → **Settings** → **Pages** → under "Build and deployment", set
   **Source** to **GitHub Actions**. (You don't need to point it at a branch
   manually — the workflow deploys directly.)

3. **Run it once manually** to generate the first version instead of waiting
   for Tuesday: Repo → **Actions** tab → select **"Weekly Cineville Amsterdam
   program"** → **Run workflow**. After it finishes (~1 minute), your page
   will be live at:
   ```
   https://<your-username>.github.io/<repo-name>/
   ```
   Bookmark that URL — that's the page you check every Tuesday.

That's it. From here it runs itself: every Tuesday around 12:00 Amsterdam
time, the Action re-scrapes filmladder.nl, rebuilds the page, commits the
updated `docs/index.html` and `data/latest.json`, and redeploys the site.

## How it works

- `scripts/fetch_program.py` fetches:
  - `filmladder.nl/amsterdam/bioscopen` for this week's full showtimes per
    cinema, and the site-wide "films deze week in première" (new releases
    this week) list.
  - `filmladder.nl/films/verwacht` for films announced in upcoming weeks,
    filtered to the next 3 weeks from run date.
- It renders everything into `docs/index.html` (dark theme, one column per
  cinema, new releases highlighted with a yellow "NEW" badge and left
  border).
- `data/latest.json` keeps a machine-readable snapshot of the same data, in
  case you want to build something else on top of it later (e.g. a diff
  against last week, a personal watchlist filter, etc).

## Known limitations (please read)

- **This is a scraper, not an official API.** Cineville doesn't expose one
  publicly. filmladder.nl's page layout could change at any point, which
  would break the parsing. If the page ever comes back mostly empty, that's
  the most likely cause — see "If it breaks" below.
- **The schedule is a DST approximation.** GitHub Actions cron runs in UTC,
  and Amsterdam shifts between UTC+1 (winter) and UTC+2 (summer). The
  workflow uses two cron lines split roughly at the DST changeover months,
  so for about a week on either side of the actual clock change (late March
  / late October) the run may land an hour off from exactly 12:00.
- **GitHub Actions' free-tier schedule isn't always to-the-minute.** Scheduled
  runs can be delayed by a few minutes (occasionally more) during periods of
  high platform load — this is a GitHub-wide limitation, not something this
  workflow controls.
- **"Announced for the next 3 weeks"** reflects whatever filmladder.nl has
  published so far — distributors often confirm cinemas city-by-city closer
  to release, so early entries may not yet say "Amsterdam" explicitly even
  if they'll eventually play here. The page marks each entry as either
  "Amsterdam confirmed" or "city list TBA" so you can tell which is which.
- **New-release highlighting** is based on filmladder's site-wide premiere
  list, then matched against each cinema's own listings — a title has to
  match exactly, so a minor formatting difference (e.g. a subtitle or
  punctuation mismatch) could occasionally cause a miss.

## If it breaks

1. Check the failed run's logs: Actions tab → the red ✗ run → expand
   "Fetch program and build page".
2. Most likely cause: filmladder.nl changed its HTML/text structure and the
   regex patterns in `scripts/fetch_program.py` (`FILM_ENTRY_RE`,
   `WEEK_HEADER_RE`, `ALL_AMSTERDAM_CINEMA_HEADERS`) no longer match. Fetch
   the page in a browser, compare to what the script expects, and adjust.
3. You're welcome to hand this repo + error message to Claude (or any LLM)
   to help patch the parsing — the script is short and heavily commented for
   exactly that purpose.

## Changing the cinema list or ordering

Edit the `CINEMAS` list near the top of `scripts/fetch_program.py`. Each
entry is `("Display Name", "filmladder.nl header text")` — the second value
has to match the cinema's exact heading text on
`filmladder.nl/amsterdam/bioscopen`.
