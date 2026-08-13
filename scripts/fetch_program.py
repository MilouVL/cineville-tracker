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
    ("Melkweg Cinema", "Melkweg Cinema"),
    ("Cinema De Balie", "De Balie"),
    ("De Uitkijk", "De Uitkijk"),
    ("Filmhuis Cavia", "Filmhuis Cavia"),
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

DAY_LABELS = ["do", "vr", "za", "zo", "ma", "di", "wo"]  # Thu..Wed

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5,
    "juni": 6, "juli": 7, "augustus": 8, "september": 9,
    "oktober": 10, "november": 11, "december": 12,
}

# A "day field" is a run of HH:MM tokens and/or literal "..." placeholders
# (filmladder shows "..." for a day with no showing). Matching it as a
# strict token pattern -- rather than a lazy generic ".*?" -- is what keeps
# entries with no star-rating from swallowing the following entry's times.
_TIME_FIELD = r"(?:(?:\d{1,2}:\d{2}|\.\.\.)\s*)*"

FILM_ENTRY_RE = re.compile(
    r"\s*(?:(?P<rating>\d\.\d)★\s+)?(?P<title>.+?)\s+"
    r"vandaag\s+do\s+(?P<do>" + _TIME_FIELD + r")\s*"
    r"morgen\s+vr\s+(?P<vr>" + _TIME_FIELD + r")\s*"
    r"zaterdag\s+za\s+(?P<za>" + _TIME_FIELD + r")\s*"
    r"zondag\s+zo\s+(?P<zo>" + _TIME_FIELD + r")\s*"
    r"maandag\s+ma\s+(?P<ma>" + _TIME_FIELD + r")\s*"
    r"dinsdag\s+di\s+(?P<di>" + _TIME_FIELD + r")\s*"
    r"woensdag\s+wo\s+(?P<wo>" + _TIME_FIELD + r")",
    re.S,
)

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
    block of flattened text."""
    entries = []
    for m in FILM_ENTRY_RE.finditer(block_text):
        title = m.group("title").strip(" -·")
        if not title or len(title) > 120:
            continue
        days = {}
        for label, key in zip(DAY_LABELS, ["do", "vr", "za", "zo", "ma", "di", "wo"]):
            raw = m.group(key)
            times = TIME_TOKEN_RE.findall(raw)
            days[label] = times
        entries.append({
            "title": title,
            "rating": m.group("rating"),
            "days": days,
        })
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
# Upcoming releases (next N weeks) from the "verwacht" page
# ----------------------------------------------------------------------------

WEEK_HEADER_RE = re.compile(
    r"(\d{1,2})\s+(\w+)\s+t/m\s+(\d{1,2})\s+(\w+)", re.IGNORECASE
)


def parse_dutch_date(day: str, month_name: str, ref_year: int) -> datetime.date | None:
    month = DUTCH_MONTHS.get(month_name.lower())
    if not month:
        return None
    return datetime.date(ref_year, month, int(day))


def get_upcoming_releases(weeks_ahead: int = 3, today: datetime.date = None) -> list:
    if today is None:
        today = datetime.date.today()
    cutoff = today + datetime.timedelta(weeks=weeks_ahead)

    soup = BeautifulSoup(fetch(VERWACHT_URL), "html.parser")

    # Find the "Verwachte films" heading, then walk siblings collecting
    # week-range headings and the <ul> of films under each, until we hit the
    # footer navigation section.
    main_heading = None
    for tag in soup.find_all(["h1", "h2"]):
        if "verwachte films" in tag.get_text(strip=True).lower():
            main_heading = tag
            break

    results = []
    if main_heading is None:
        return results

    current_week_label = None
    current_week_start = None

    node = main_heading.find_next_sibling()
    while node is not None:
        node_text = node.get_text(" ", strip=True)

        if node.name in ("h2", "h3", "p", "strong") and WEEK_HEADER_RE.search(node_text):
            m = WEEK_HEADER_RE.search(node_text)
            d1, mon1, d2, mon2 = m.groups()
            start_date = parse_dutch_date(d1, mon1, today.year)
            # handle year rollover (e.g. Dec -> Jan week straddling new year)
            if start_date and start_date < today - datetime.timedelta(days=200):
                start_date = parse_dutch_date(d1, mon1, today.year + 1)
            current_week_label = node_text
            current_week_start = start_date

        elif node.name == "ul" and current_week_start is not None:
            if current_week_start > cutoff:
                break
            for li in node.find_all("li"):
                a = li.find("a")
                if not a:
                    continue
                title = a.get("title") or a.get_text(strip=True)
                li_text = li.get_text(" ", strip=True)
                amsterdam_mentioned = "amsterdam" in li_text.lower()
                results.append({
                    "title": title,
                    "week_label": current_week_label,
                    "week_start": current_week_start.isoformat(),
                    "amsterdam_confirmed": amsterdam_mentioned,
                })

        # Stop once we clearly leave the "verwacht" listing section.
        if node.name == "h2" and "steden met bioscopen" in node_text.lower():
            break

        node = node.find_next_sibling()

    return results


# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------

def render_html(program: dict, new_releases: list, upcoming: list, generated_at: str) -> str:
    new_releases_lower = {t.lower() for t in new_releases}

    def cell_html(entries):
        if not entries:
            return '<div class="empty">no listings</div>'
        parts = []
        for e in entries:
            is_new = e["title"].lower() in new_releases_lower
            css = "film new-release" if is_new else "film"
            badge = '<span class="badge">NEW</span>' if is_new else ""
            day_rows = []
            for label in DAY_LABELS:
                times = e["days"].get(label, [])
                if times:
                    day_rows.append(
                        f'<div class="day-row"><span class="day">{label}</span>'
                        f'<span class="times">{" ".join(times)}</span></div>'
                    )
            rating = f'<span class="rating">{e["rating"]}&#9733;</span>' if e["rating"] else ""
            parts.append(
                f'<div class="{css}">{badge}<div class="title">{html.escape(e["title"])} '
                f'{rating}</div><div class="times-block">{"".join(day_rows)}</div></div>'
            )
        return "".join(parts)

    cinema_columns = "".join(
        f'<th>{html.escape(name)}</th>' for name, _ in CINEMAS
    )
    cinema_cells = "".join(
        f'<td>{cell_html(program.get(name, []))}</td>' for name, _ in CINEMAS
    )

    new_release_items = "".join(
        f'<li>{html.escape(t)}</li>' for t in new_releases
    ) or "<li><em>none detected this week</em></li>"

    # group upcoming by week_label, preserving order
    upcoming_by_week = {}
    for item in upcoming:
        upcoming_by_week.setdefault(item["week_label"], []).append(item)

    upcoming_html = ""
    for week_label, items in upcoming_by_week.items():
        rows = "".join(
            f'<li>{html.escape(it["title"])}'
            + (" <span class=\"conf\">(Amsterdam confirmed)</span>" if it["amsterdam_confirmed"] else " <span class=\"unconf\">(city list TBA)</span>")
            + "</li>"
            for it in items
        )
        upcoming_html += f'<h3>{html.escape(week_label)}</h3><ul>{rows}</ul>'

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
  th, td {{ border: 1px solid #333; vertical-align: top; padding: 8px; width: {100/len(CINEMAS):.2f}%; }}
  th {{ background: #1b1b1b; position: sticky; top: 0; font-size: 0.8rem; }}
  .film {{ border-bottom: 1px solid #292929; padding: 6px 0; position: relative; }}
  .film:last-child {{ border-bottom: none; }}
  .film.new-release {{ background: rgba(255, 204, 2, 0.08); border-left: 3px solid #ffcc02; padding-left: 6px; }}
  .badge {{ display: inline-block; background: #ffcc02; color: #111; font-size: 0.6rem;
           font-weight: 700; padding: 1px 5px; border-radius: 3px; margin-right: 4px; vertical-align: middle; }}
  .title {{ font-size: 0.78rem; font-weight: 600; margin-bottom: 3px; }}
  .rating {{ color: #ffcc02; font-weight: 400; font-size: 0.7rem; }}
  .times-block {{ font-size: 0.68rem; color: #bbb; }}
  .day-row {{ display: flex; gap: 6px; }}
  .day {{ text-transform: uppercase; color: #777; width: 18px; flex-shrink: 0; }}
  .empty {{ color: #555; font-style: italic; font-size: 0.75rem; }}
  section {{ margin-top: 36px; }}
  section h2 {{ font-size: 1.1rem; border-bottom: 1px solid #333; padding-bottom: 6px; }}
  ul {{ font-size: 0.85rem; line-height: 1.6; }}
  .conf {{ color: #6fd66f; font-size: 0.75rem; }}
  .unconf {{ color: #888; font-size: 0.75rem; }}
</style>
</head>
<body>
  <h1>🎬 Cineville Amsterdam — Weekly Program</h1>
  <div class="meta">Generated {html.escape(generated_at)} · source: filmladder.nl · cinemas ordered by proximity to 1075&nbsp;TR</div>

  <table>
    <thead><tr>{cinema_columns}</tr></thead>
    <tbody><tr>{cinema_cells}</tr></tbody>
  </table>

  <section>
    <h2>🌟 New releases this week</h2>
    <ul>{new_release_items}</ul>
  </section>

  <section>
    <h2>📅 Announced for the next 3 weeks</h2>
    {upcoming_html or "<p>Nothing found.</p>"}
  </section>
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
    upcoming = get_upcoming_releases(weeks_ahead=3)

    out_html = render_html(program, new_releases, upcoming, generated_at)

    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "index.html").write_text(out_html, encoding="utf-8")

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    snapshot = {
        "generated_at": generated_at,
        "program": program,
        "new_releases": new_releases,
        "upcoming": upcoming,
    }
    (data_dir / "latest.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Wrote docs/index.html and data/latest.json at {generated_at}")


if __name__ == "__main__":
    main()
