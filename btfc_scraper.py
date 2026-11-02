#!/usr/bin/env python3
"""
Bradford Town FC scraper - fixtures/results and league table.

Standard library only (no pip installs), so it runs fine on a Raspberry Pi 1.
Works on Python 3.5+.

Outputs (in OUTPUT_DIR), in the format chosen by OUTPUT_FORMAT:
    fixtures.csv      + league_table.csv       (OUTPUT_FORMAT = "csv")
    fixtures.json     + league_table.json      (OUTPUT_FORMAT = "json")

Existing files are only replaced if the new scrape looks sane, so a bad
download never wipes your good data.
"""

import csv
import datetime
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

# ---------------------------------------------------------------- config ----
FIXTURES_URL = "https://bradfordtownfc.com/fixtures-results-2026-27/"
LEAGUE_URL = "https://bradfordtownfc.com/league-table/"
SEASON_START_YEAR = 2026          # season 2026/27 -> Aug-Dec 2026, Jan-Apr 2027
OUTPUT_FORMAT = "csv"          # "csv" or "json"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (compatible; BTFC-personal-scraper/1.0)"
# --------------------------------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DATE_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})(?:st|nd|rd|th)$")
SCORE_RE = re.compile(r"^([WDL])\s*(\d+)\s*-\s*(\d+)$")
TEAM_ID_RE = re.compile(r"teamID=(\d+)")


class TableParser(HTMLParser):
    """Collects every <table> as a list of rows; each cell is (text, href)."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.tables = []
        self._table = None
        self._row = None
        self._cell_text = None
        self._cell_href = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell_text = []
            self._cell_href = None
        elif tag == "a" and self._cell_text is not None:
            href = dict(attrs).get("href")
            if href and not self._cell_href:
                self._cell_href = href
        elif tag == "br" and self._cell_text is not None:
            self._cell_text.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell_text is not None:
            text = " ".join("".join(self._cell_text).split())
            self._row.append((text, self._cell_href))
            self._cell_text = None
        elif tag == "tr" and self._row is not None:
            if self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell_text is not None:
            self._cell_text.append(data)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def parse_tables(html):
    p = TableParser()
    p.feed(html)
    return p.tables


# ------------------------------------------------------------- fixtures ----
def parse_fixtures(html):
    tables = parse_tables(html)
    table = None
    for t in tables:
        if t and t[0] and t[0][0][0].strip().lower() == "date":
            table = t
            break
    if table is None:
        raise ValueError("Fixtures table not found - has the page layout changed?")

    fixtures = []
    month = None
    for row in table[1:]:
        cells = [c[0] for c in row] + [""] * 7
        first = cells[0]

        # Month heading row, e.g. "August"
        if first in MONTHS:
            month = first
            continue

        m = DATE_RE.match(first)
        if not m or month is None:
            continue  # blank rows, notes, legend

        day = int(m.group(2))
        month_num = MONTHS.index(month) + 1
        year = SEASON_START_YEAR if month_num >= 7 else SEASON_START_YEAR + 1
        try:
            iso = datetime.date(year, month_num, day).isoformat()
        except ValueError:
            iso = ""

        venue = cells[3].strip().upper()
        result_raw = cells[4].strip()
        sm = SCORE_RE.match(result_raw)
        outcome = sm.group(1) if sm else ""
        goals_for = int(sm.group(2)) if sm else None
        goals_against = int(sm.group(3)) if sm else None

        # Home rows are bold/UPPERCASE on the site, so normalise the casing
        opponent = cells[2].strip()
        if opponent.isupper():
            opponent = opponent.title()

        attendance = cells[5].strip()
        fixtures.append({
            "date": iso,
            "date_text": first,
            "competition": cells[1].strip(),
            "opponent": opponent,
            "venue": venue,                       # H or A
            "result_raw": result_raw,             # e.g. "W 2-0", "P-P", ""
            "outcome": outcome,                   # W / D / L / ""
            "goals_for": goals_for,               # Bradford first
            "goals_against": goals_against,
            "attendance": int(attendance) if attendance.isdigit() else None,
            "match_sponsor": cells[6].strip(),
            "played": bool(sm),
        })
    return fixtures


# ---------------------------------------------------------- league table ----
def to_int(s):
    try:
        return int(s.replace("+", "").strip())
    except ValueError:
        return None


def parse_league(html):
    tables = parse_tables(html)
    table = None
    for t in tables:
        if t and t[0] and t[0][0][0].strip().upper() == "POS":
            table = t
            break
    if table is None:
        raise ValueError("League table not found - has the page layout changed?")

    rows = []
    for row in table[1:]:
        if len(row) < 8:
            continue
        pos = to_int(row[0][0])
        if pos is None:
            continue
        href = row[1][1] or ""
        tid = TEAM_ID_RE.search(href)
        rows.append({
            "pos": pos,
            "team": row[1][0],
            "played": to_int(row[2][0]),
            "won": to_int(row[3][0]),
            "drawn": to_int(row[4][0]),
            "lost": to_int(row[5][0]),
            "goal_diff": to_int(row[6][0]),
            "points": to_int(row[7][0]),
            "team_id": tid.group(1) if tid else "",
            "team_url": href,
        })
    return rows


# ---------------------------------------------------------------- output ----
def save(rows, basename, min_rows):
    """Write one file atomically; refuse to overwrite with suspiciously little data."""
    if len(rows) < min_rows:
        raise ValueError("{}: only {} rows scraped (expected >= {}); keeping old file"
                         .format(basename, len(rows), min_rows))
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    path = os.path.join(OUTPUT_DIR, basename + "." + OUTPUT_FORMAT)
    tmp = path + ".tmp"

    if OUTPUT_FORMAT == "json":
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
    else:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    os.replace(tmp, path)
    print("Saved {} rows -> {}".format(len(rows), path))


def main():
    errors = 0

    try:
        fixtures = parse_fixtures(fetch(FIXTURES_URL))
        save(fixtures, "fixtures", min_rows=10)
        played = sum(1 for x in fixtures if x["played"])
        print("  {} fixtures, {} played".format(len(fixtures), played))
    except Exception as e:
        errors += 1
        print("Fixtures failed: {}".format(e), file=sys.stderr)

    try:
        league = parse_league(fetch(LEAGUE_URL))
        save(league, "league_table", min_rows=10)
    except Exception as e:
        errors += 1
        print("League table failed: {}".format(e), file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
