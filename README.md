# Cineville Amsterdam — Weekly Program Tracker

Automatically builds a webpage every Tuesday and Thursday around 12:00
(Europe/Amsterdam) showing the film program for 14 Cineville-affiliated Amsterdam cinemas,
ordered by proximity to postcode 1075 TR, with new releases highlighted and
an "announced for the next 3 weeks" section.

**Cinemas covered:** Rialto VU, LAB111, Filmhallen, Rialto De Pijp,
Cinecenter, Cinema De Balie, De Uitkijk, Het Ketelhuis, Kriterion,
The Movies, EYE Filmmuseum, Studio/K.

**Page layout:** new releases (with a Rotten Tomatoes search link for each)
are listed first, then what's announced for the next 3 weeks, then the full
program as a table with one column per cinema. Each day gets its own
labeled section (e.g. "Monday, 17 August") with three rows underneath —
Morning (before 12:00), Afternoon (12:00–18:00), and Evening (after
18:00) — each showing exact start times.

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
   for it instead of waiting: Repo → **Actions** tab → select **"Cineville
   Amsterdam program"** → **Run workflow**. After it finishes (~1 minute),
   your page will be live at:
   ```
   https://<your-username>.github.io/<repo-name>/
   ```
   Bookmark that URL — that's the page you check.

That's it. From here it runs itself: every Tuesday and Thursday around
12:00 Amsterdam time, the Action re-scrapes filmladder.nl, rebuilds the
page, commits the updated `docs/index.html` and `data/latest.json`, and
redeploys the site. Thursday's run is the one that reliably catches the new
week — filmladder.nl only ever shows the currently active week, so it
doesn't reflect the new schedule until the new week actually starts.
Tuesday's run mostly reconfirms the outgoing week, though it can still
catch late schedule changes.

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
- **The day-columns are keyed off the 2-letter weekday abbreviation
  (do/vr/za/zo/ma/di/wo), not the Dutch word next to it.** filmladder.nl
  labels the 7 day-columns with a rotating mix of relative words
  ("vandaag"/"morgen") and absolute weekday names depending on what day the
  page happens to load — e.g. fetched on a Thursday the columns read
  "vandaag do, morgen vr, zaterdag za, ...", but fetched on a Friday they
  read "vandaag vr, morgen za, zondag zo, ..." instead. (This broke the
  tracker the first time it ran on a day other than Thursday — every cell
  showed "no listings" — before the parser was changed to key off the
  reliable 2-letter abbreviation instead of the rotating Dutch word. A
  second, related bug showed up later: the regex only recognized 6 of the 7
  possible absolute weekday names — "vrijdag" was missing — so on a run
  where Friday was neither "today" nor "tomorrow", that one day-field
  failed to match and desynced parsing for every film after it, producing
  garbled titles like "vrijdag vr" and an empty Friday column. Fixed by
  listing all 7 Dutch weekday names.)
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
- **Rotten Tomatoes links go to a search results page, not the movie page
  directly.** RT's URL slugs (`/m/title_year` vs `/m/title`, etc.) aren't
  consistent enough to construct reliably, so each new release links to
  `rottentomatoes.com/search?search=<title>` instead — one click from the
  real page, and it won't ever 404 the way a guessed direct link could.
- **Calendar dates are computed, not scraped.** filmladder.nl's day columns
  never state an actual date, only a weekday abbreviation, so each date
  shown (e.g. "Monday, 17 August") is calculated from the run date assuming
  the current Thu–Wed "speelweek" — this is reliable under the standard
  cycle but would be off if a cinema ever ran an irregular-length week.
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
