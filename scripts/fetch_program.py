#!/usr/bin/env python3
"""
Cineville Amsterdam weekly program tracker.

Fetches the current week's showtimes for a fixed list of Cineville-affiliated
Amsterdam cinemas from filmladder.nl, plus the "new this week" premiere list
and the "coming in the next few weeks" list, and renders a static HTML page.

Data source: filmladder.nl (not an official Cineville API -- there isn't a
public one). This is a best-effort scraper based on the site's rendered text
structure as of build time. If filmladder.nl changes its page layout, the
regexes below may need updating -- see README.md for how to debug that.
"""

import re
import json
import html
import datetime
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

BIOSCOPEN_URL = "https://www.filmladder.nl/amsterdam/bioscopen"
VERWACHT_URL = "https://www.filmladder.nl/films/verwacht"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CinevilleTrackerBot/1.0; "
                  "+https://github.com/) personal-use weekly digest"
}

# The order here is proximity-to-1075TR order (nearest first), matching what
# was worked out earlier. Left value = display name, right value = exact
# header text as it appears on the filmladder.nl Amsterdam cinemas page.
CINEMAS = [
    ("Rialto VU", "Rialto VU"),
    ("LAB111", "LAB111"),
    ("Filmhallen", "Filmhallen"),
    ("Rialto De Pijp", "Rialto De Pijp"),
    ("Cinecenter", "Cinecenter"),
    ("Cinema De Balie", "De Balie"),
    ("De Uitkijk", "De Uitkijk"),
    ("Het Ketelhuis", "Het Ketelhuis"),
    ("Kriterion", "Kriterion"),
    ("The Movies", "The Movies"),
    ("EYE Filmmuseum", "EYE"),
    ("Studio/K", "Studio/K"),
]

# Every cinema header that can appear on the Amsterdam bioscopen page, used
# only to find the boundaries between cinema sections in the flattened page
# text (includes non-Cineville cinemas like Pathé/Vue so their listings don't
# leak into a Cineville cinema's block).
ALL_AMSTERDAM_CINEMA_HEADERS = [
    "Bijlmerbios", "Cinecenter", "Cinema De Vlugt", "Cinema The Pulse",
    "De Balie", "De Uitkijk", "EYE", "FC Hyena", "Filmhallen",
    "Filmhuis Cavia", "Het Documentaire Paviljoen", "Het Ketelhuis",
    "Kriterion", "LAB111", "Melkweg Cinema", "Pathé Amsterdam Noord",
    "Pathé Arena", "Pathé City", "Pathé de Munt", "Pathé Tuschinski",
    "Rialto De Pijp", "Rialto VU", "Studio/K", "The Movies", "Vue Amsterdam",
]

DAY_LABELS = ["do", "vr", "za", "zo", "ma", "di", "wo"]  # Thu..Wed (parsing order)

# Display order: Monday first, as requested, rather than the Thu-Wed
# "speelweek" order the data is parsed in. (key, English label)
DISPLAY_DAY_ORDER = [
    ("ma", "Monday"),
    ("di", "Tuesday"),
    ("wo", "Wednesday"),
    ("do", "Thursday"),
    ("vr", "Friday"),
    ("za", "Saturday"),
    ("zo", "Sunday"),
]

# Offset (in days) of each weekday abbreviation from the Thursday that
# starts the current "speelweek" -- used to attach an actual calendar date
# to each day column (do/vr/za/zo/ma/di/wo always mean Thu/Fri/Sat/Sun/
# Mon/Tue/Wed of THIS programming week, regardless of what day the site
# happened to label them with -- see the note on ONE_DAY_FIELD_RE below).
_WEEKDAY_OFFSET_FROM_THURSDAY = {
    "do": 0, "vr": 1, "za": 2, "zo": 3, "ma": 4, "di": 5, "wo": 6,
}

# Time-of-day buckets a showing's start time is sorted into.
# (bucket_key, label, start_hour_inclusive, end_hour_exclusive)
TIME_PERIODS = [
    ("morning", "Morning", 0, 12),
    ("afternoon", "Afternoon", 12, 18),
    ("evening", "Evening", 18, 24),
]


def compute_day_dates(today: datetime.date) -> dict:
    """Map each weekday abbreviation to its actual calendar date within the
    current Thu-Wed programming week that contains `today`."""
    days_since_thursday = (today.weekday() - 3) % 7  # Mon=0 ... Thu=3 ... Sun=6
    this_weeks_thursday = today - datetime.timedelta(days=days_since_thursday)
    return {
        abbr: this_weeks_thursday + datetime.timedelta(days=offset)
        for abbr, offset in _WEEKDAY_OFFSET_FROM_THURSDAY.items()
    }


def period_of(time_str: str) -> str:
    hour = int(time_str.split(":")[0])
    for key, _label, start, end in TIME_PERIODS:
        if start <= hour < end:
            return key
    return "evening"  # fallback for any out-of-range/odd timestamp

ROTTEN_TOMATOES_SEARCH_URL = "https://www.rottentomatoes.com/search?search={query}"

# A "day field" is a run of HH:MM tokens and/or literal "..." placeholders
# (filmladder shows "..." for a day with no showing).
_TIME_FIELD = r"(?:(?:\d{1,2}:\d{2}|\.\.\.)\s*)*"

# IMPORTANT: filmladder.nl's 7 day-columns are labelled with a *rotating*
# pair of (relative-or-absolute Dutch day word, 2-letter weekday
# abbreviation) -- e.g. on a page fetched on a Thursday the columns read
# "vandaag do, morgen vr, zaterdag za, zondag zo, maandag ma, dinsdag di,
# woensdag wo", but fetched on a Friday they read "vandaag vr, morgen za,
# zondag zo, maandag ma, dinsdag di, woensdag wo, donderdag do" instead --
# "zaterdag" drops out and "donderdag" appears, because the Dutch word is
# always relative-then-absolute-going-forward from whatever day it is when
# the page loads. The word itself is therefore NOT a reliable anchor for a
# regex run on a schedule (this broke the tracker the first time it ran on
# a non-Thursday). The 2-letter abbreviation (do/vr/za/zo/ma/di/wo) is
# always reliable -- it always means Thu/Fri/Sat/Sun/Mon/Tue/Wed regardless
# of which Dutch word precedes it or what order the 7 columns come in -- so
# that's what parsing keys off instead.
#
# The word list below has to cover ALL 7 possible absolute weekday names,
# not just the ones observed in one sample fetch -- any of the 7 (not just
# "zaterdag"/"donderdag") can show up as the absolute label depending on
# which 2 days are "today"/"tomorrow" on a given run. (An earlier version
# of this list omitted "vrijdag" and broke parsing on any run where Friday
# wasn't today or tomorrow -- every weekday name must be present here.)
_RELATIVE_DAY_WORD = (
    r"(?:vandaag|morgen"
    r"|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)"
)

ONE_DAY_FIELD_RE = re.compile(
    _RELATIVE_DAY_WORD + r"\s+(?P<abbr>do|vr|za|zo|ma|di|wo)\s+"
    r"(?P<times>" + _TIME_FIELD + r")"
)

RATING_RE = re.compile(r"(\d\.\d)★\s*(.*)$", re.S)

TIME_TOKEN_RE = re.compile(r"\d{1,2}:\d{2}")


# ----------------------------------------------------------------------------
# Fetch helpers
# ----------------------------------------------------------------------------

def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def flatten_text(html_doc: str) -> str:
    soup = BeautifulSoup(html_doc, "html.parser")
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------------------------------------------------------
# This week's program (per cinema)
# ----------------------------------------------------------------------------

def split_into_cinema_blocks(full_text: str) -> dict:
    """Split the flattened Amsterdam-bioscopen page text into
    {header_name: block_text} using every known Amsterdam cinema header as a
    delimiter, so a Cineville cinema's block never bleeds into a
    non-Cineville neighbour's listings."""
    headers_sorted = sorted(ALL_AMSTERDAM_CINEMA_HEADERS, key=len, reverse=True)
    pattern = re.compile("(" + "|".join(re.escape(h) for h in headers_sorted) + ")")
    parts = pattern.split(full_text)

    blocks = {}
    i = 1  # parts[0] is whatever precedes the first header
    while i < len(parts) - 1:
        header = parts[i]
        content = parts[i + 1]
        # If the same header text appears more than once (shouldn't happen
        # normally), keep the first / longest occurrence.
        if header not in blocks or len(content) > len(blocks[header]):
            blocks[header] = content
        i += 2
    return blocks


def parse_cinema_block(block_text: str) -> list:
    """Extract film entries (title, rating, per-day times) from one cinema's
    block of flattened text. Each film always has exactly 7 consecutive
    day-fields (see ONE_DAY_FIELD_RE), so day-fields are found first and
    grouped into runs of 7; the title/rating for each run is whatever text
    sits between the end of the previous run and the start of this one."""
    day_matches = list(ONE_DAY_FIELD_RE.finditer(block_text))
    usable = len(day_matches) - (len(day_matches) % 7)

    entries = []
    prev_end = 0
    for i in range(0, usable, 7):
        group = day_matches[i:i + 7]
        gap_text = block_text[prev_end:group[0].start()]
        prev_end = group[-1].end()

        rating_match = RATING_RE.search(gap_text)
        if rating_match:
            rating, title = rating_match.group(1), rating_match.group(2)
        else:
            rating, title = None, gap_text
        title = title.strip(" -·")
        if not title or len(title) > 120:
            continue

        days = {}
        for field_match in group:
            days[field_match.group("abbr")] = TIME_TOKEN_RE.findall(
                field_match.group("times")
            )
        entries.append({"title": title, "rating": rating, "days": days})
    return entries


def get_this_week_program() -> dict:
    page_text = flatten_text(fetch(BIOSCOPEN_URL))
    blocks = split_into_cinema_blocks(page_text)

    program = {}
    for display_name, header_name in CINEMAS:
        block = blocks.get(header_name, "")
        if "geen voorstellingen" in block[:60]:
            program[display_name] = []
        else:
            program[display_name] = parse_cinema_block(block)
    return program


# ----------------------------------------------------------------------------
# New releases this week (site-wide "films deze week in premiere" list)
# ----------------------------------------------------------------------------

def get_new_releases_this_week() -> list:
    soup = BeautifulSoup(fetch(VERWACHT_URL), "html.parser")
    text_all = soup.get_text(separator="\n")
    lines = [l.strip() for l in text_all.split("\n") if l.strip()]

    try:
        start = next(i for i, l in enumerate(lines)
                     if "films deze week in premi" in l.lower())
    except StopIteration:
        return []

    titles = []
    for l in lines[start + 1:]:
        if l.lower().startswith(("filmladder", "missie", "contact", "website",
                                   "©", "steden", "films in nederland")):
            break
        if len(l) < 2 or len(l) > 100:
            break
        titles.append(l)
        if len(titles) >= 30:
            break
    return titles


# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------

FILTER_JS = """
(function () {
  var dayDD = document.getElementById('filter-day');
  var perDD = document.getElementById('filter-period');
  var rows = document.querySelectorAll('tbody tr');
  var noMatch = document.getElementById('no-match');

  function checked(dd) {
    return Array.prototype.map.call(
      dd.querySelectorAll('input:checked'), function (i) { return i.value; });
  }
  function label(dd) {
    var total = dd.querySelectorAll('input').length;
    var n = checked(dd).length;
    var name = dd.getAttribute('data-label');
    return n === total ? name + ': all' : n === 0 ? name + ': none' : name + ': ' + n + ' selected';
  }
  function apply() {
    var days = checked(dayDD), pers = checked(perDD), any = false;
    Array.prototype.forEach.call(rows, function (r) {
      var dayOk = days.indexOf(r.getAttribute('data-day')) !== -1;
      var perAttr = r.getAttribute('data-period');
      var show = dayOk && pers.length > 0 && (perAttr === null || pers.indexOf(perAttr) !== -1);
      r.style.display = show ? '' : 'none';
      if (show) any = true;
    });
    noMatch.style.display = any ? 'none' : 'block';
    dayDD.querySelector('.dd-btn').textContent = label(dayDD);
    perDD.querySelector('.dd-btn').textContent = label(perDD);
  }
  [dayDD, perDD].forEach(function (dd) {
    dd.querySelector('.dd-btn').addEventListener('click', function (e) {
      e.stopPropagation();
      var wasOpen = dd.classList.contains('open');
      dayDD.classList.remove('open'); perDD.classList.remove('open');
      if (!wasOpen) dd.classList.add('open');
    });
    dd.querySelector('.dd-panel').addEventListener('click', function (e) { e.stopPropagation(); });
    dd.addEventListener('change', apply);
    Array.prototype.forEach.call(dd.querySelectorAll('[data-act]'), function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var on = a.getAttribute('data-act') === 'all';
        Array.prototype.forEach.call(dd.querySelectorAll('input'), function (i) { i.checked = on; });
        apply();
      });
    });
  });
  document.addEventListener('click', function () {
    dayDD.classList.remove('open'); perDD.classList.remove('open');
  });
  apply();
})();
"""


def rt_search_url(title: str) -> str:
    """Rotten Tomatoes doesn't have a guessable direct movie-page URL (the
    slug format isn't consistent, e.g. /m/title_year vs /m/title), so this
    links to a live RT search for the title instead -- one click from the
    real page, and it never 404s the way a guessed direct link could."""
    return ROTTEN_TOMATOES_SEARCH_URL.format(query=urllib.parse.quote(title))


def build_day_grid(program: dict) -> dict:
    """Reshape {cinema: [film entries...]} into
    {day_key: {cinema_name: [(time, title, rating), ...sorted by time]}}."""
    grid = {key: {name: [] for name, _ in CINEMAS} for key, _ in DISPLAY_DAY_ORDER}
    for cinema_name, entries in program.items():
        for e in entries:
            for day_key, _ in DISPLAY_DAY_ORDER:
                for t in e["days"].get(day_key, []):
                    grid[day_key][cinema_name].append((t, e["title"], e["rating"]))
    for day_key in grid:
        for cinema_name in grid[day_key]:
            grid[day_key][cinema_name].sort(key=lambda x: x[0])
    return grid


def render_html(program: dict, new_releases: list, generated_at: str,
                 today: datetime.date = None) -> str:
    if today is None:
        today = datetime.date.today()
    new_releases_lower = {t.lower() for t in new_releases}
    day_grid = build_day_grid(program)
    day_dates = compute_day_dates(today)

    def showing_html(time_, title, rating):
        is_new = title.lower() in new_releases_lower
        css = "showing new-release" if is_new else "showing"
        badge = '<span class="badge">NEW</span>' if is_new else ""
        rating_html = f'<span class="rating">{rating}&#9733;</span>' if rating else ""
        return (
            f'<div class="{css}">{badge}<span class="time">{time_}</span> '
            f'<span class="title">{html.escape(title)}</span>{rating_html}</div>'
        )

    def period_cell_html(day_key, cinema_name, period_key):
        showings = [
            s for s in day_grid[day_key][cinema_name] if period_of(s[0]) == period_key
        ]
        if not showings:
            return '<div class="empty">—</div>'
        return "".join(showing_html(t, title, rating) for t, title, rating in showings)

    cinema_columns = "".join(
        f'<th>{html.escape(name)}</th>' for name, _ in CINEMAS
    )
    n_cols = len(CINEMAS) + 1

    day_rows_html = ""
    for day_key, day_label in DISPLAY_DAY_ORDER:
        date_str = day_dates[day_key].strftime("%-d %B")
        day_rows_html += (
            f'<tr class="day-divider" data-day="{day_key}"><th colspan="{n_cols}">'
            f'{day_label}, {date_str}</th></tr>'
        )
        for period_key, period_label, _start, _end in TIME_PERIODS:
            cells = "".join(
                f'<td>{period_cell_html(day_key, name, period_key)}</td>'
                for name, _ in CINEMAS
            )
            day_rows_html += (
                f'<tr class="period-row" data-day="{day_key}" data-period="{period_key}">'
                f'<th class="period-head" scope="row">{period_label}</th>{cells}</tr>'
            )

    def filter_dropdown(filter_id, label, options):
        items = "".join(
            f'<label class="opt"><input type="checkbox" value="{value}" checked> '
            f'{html.escape(text)}</label>'
            for value, text in options
        )
        return (
            f'<div class="dd" id="{filter_id}" data-label="{label}">'
            f'<button type="button" class="dd-btn"></button>'
            f'<div class="dd-panel">'
            f'<div class="dd-actions"><a href="#" data-act="all">Select all</a>'
            f' · <a href="#" data-act="none">Clear</a></div>{items}</div></div>'
        )

    day_options = [
        (key, f"{label}, {day_dates[key].strftime('%-d %B')}")
        for key, label in DISPLAY_DAY_ORDER
    ]
    period_options = [(key, label) for key, label, _s, _e in TIME_PERIODS]
    filter_bar_html = (
        '<div class="filters">'
        + filter_dropdown("filter-day", "Day", day_options)
        + filter_dropdown("filter-period", "Part of day", period_options)
        + '</div>'
    )

    new_release_items = "".join(
        f'<li><a href="{rt_search_url(t)}" target="_blank" rel="noopener">'
        f'{html.escape(t)}</a> <span class="rt-hint">(Rotten Tomatoes ↗)</span></li>'
        for t in new_releases
    ) or "<li><em>none detected this week</em></li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cineville Amsterdam — Weekly Program</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 24px; background: #111; color: #eee; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0; }}
  .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 24px; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  th, td {{ border: 1px solid #333; vertical-align: top; padding: 8px;
           width: {100/(len(CINEMAS)+1):.2f}%; }}
  th {{ background: #1b1b1b; font-size: 0.8rem; }}
  th.period-head {{ position: sticky; left: 0; background: #1b1b1b; width: 90px;
                    text-transform: uppercase; color: #ffcc02; font-size: 0.7rem; }}
  tr.day-divider th {{ background: #24240a; color: #ffcc02; font-size: 0.9rem;
                       text-align: left; padding: 10px 8px; border-top: 2px solid #ffcc02; }}
  thead th {{ position: sticky; top: 0; z-index: 2; }}
  thead th.period-head {{ z-index: 3; }}
  .showing {{ border-bottom: 1px solid #292929; padding: 5px 0; }}
  .showing:last-child {{ border-bottom: none; }}
  .showing.new-release {{ background: rgba(255, 204, 2, 0.08); border-left: 3px solid #ffcc02; padding-left: 6px; }}
  .badge {{ display: inline-block; background: #ffcc02; color: #111; font-size: 0.6rem;
           font-weight: 700; padding: 1px 5px; border-radius: 3px; margin-right: 4px; vertical-align: middle; }}
  .time {{ font-weight: 700; font-size: 0.75rem; color: #ffcc02; }}
  .title {{ font-size: 0.75rem; }}
  .rating {{ color: #ffcc02; font-weight: 400; font-size: 0.68rem; margin-left: 4px; }}
  .empty {{ color: #555; font-style: italic; font-size: 0.75rem; }}
  section {{ margin-bottom: 36px; }}
  section h2 {{ font-size: 1.1rem; border-bottom: 1px solid #333; padding-bottom: 6px; }}
  ul {{ font-size: 0.85rem; line-height: 1.8; padding-left: 20px; }}
  #new-releases a {{ color: #ffcc02; font-weight: 600; text-decoration: none; }}
  #new-releases a:hover {{ text-decoration: underline; }}
  .rt-hint {{ color: #777; font-size: 0.75rem; font-weight: 400; }}
  .filters {{ display: flex; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }}
  .dd {{ position: relative; }}
  .dd-btn {{ background: #1b1b1b; color: #eee; border: 1px solid #444; border-radius: 6px;
            padding: 8px 14px; font-size: 0.85rem; cursor: pointer; min-width: 170px; text-align: left; }}
  .dd-btn::after {{ content: "▾"; float: right; margin-left: 10px; color: #ffcc02; }}
  .dd-btn:hover {{ border-color: #ffcc02; }}
  .dd-panel {{ display: none; position: absolute; top: calc(100% + 4px); left: 0; z-index: 10;
              background: #1b1b1b; border: 1px solid #444; border-radius: 6px;
              padding: 8px 12px; min-width: 220px; box-shadow: 0 6px 20px rgba(0,0,0,0.5); }}
  .dd.open .dd-panel {{ display: block; }}
  .dd-actions {{ font-size: 0.75rem; margin-bottom: 6px; color: #777; }}
  .dd-actions a {{ color: #ffcc02; text-decoration: none; }}
  .opt {{ display: block; padding: 4px 0; font-size: 0.85rem; cursor: pointer; }}
  .opt input {{ accent-color: #ffcc02; margin-right: 6px; }}
  .no-match {{ color: #888; font-style: italic; padding: 16px 0; display: none; }}
</style>
</head>
<body>
  <h1>🎬 Cineville Amsterdam — Weekly Program</h1>
  <div class="meta">Generated {html.escape(generated_at)} · source: filmladder.nl · cinemas ordered by proximity to 1075&nbsp;TR</div>

  <section id="new-releases">
    <h2>🌟 New releases this week</h2>
    <ul>{new_release_items}</ul>
  </section>

  <section>
    <h2>🗓️ Full program by day</h2>
    {filter_bar_html}
    <div class="no-match" id="no-match">Nothing selected — tick at least one day and one part of the day.</div>
    <table>
      <thead><tr><th class="period-head">Time</th>{cinema_columns}</tr></thead>
      <tbody>{day_rows_html}</tbody>
    </table>
  </section>
<script>{FILTER_JS}</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    program = get_this_week_program()
    new_releases = get_new_releases_this_week()

    out_html = render_html(program, new_releases, generated_at)

    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "index.html").write_text(out_html, encoding="utf-8")

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    snapshot = {
        "generated_at": generated_at,
        "program": program,
        "new_releases": new_releases,
    }
    (data_dir / "latest.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Wrote docs/index.html and data/latest.json at {generated_at}")


if __name__ == "__main__":
    main()
