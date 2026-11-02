#!/usr/bin/env python3
"""
Turn data/fixtures.csv (or .json) into data/bradford_town.ics

Standard library only; works on Python 3.5+.

Kick-off rules (from the club's note):
  - Saturday / Sunday / Bank Holiday  -> 15:00
  - Anything else (midweek)           -> 19:30
  - Add exceptions to KICKOFF_OVERRIDES ("otherwise stated" games)
Every event lasts 2.5 hours.
"""

import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT_FILE = os.path.join(DATA_DIR, "bradford_town.ics")

CAL_NAME = "Bradford Town FC"
HOME_GROUND = "Bradford Town FC, Trowbridge Road, Bradford on Avon"  # check/edit
DURATION = datetime.timedelta(hours=2, minutes=30)
SATURDAY_KO = (15, 0)
MIDWEEK_KO = (19, 30)

# England bank holidays for the 2026/27 season (edit for future seasons)
BANK_HOLIDAYS = {
    "2026-08-31", "2026-12-25", "2026-12-28",
    "2027-01-01", "2027-03-26", "2027-03-29",
}

# Games with a different kick-off, e.g. {"2026-11-03": "19:45"}
KICKOFF_OVERRIDES = {}

COMPETITIONS = {
    "HLPD": "Hellenic League Premier Division",
    "FAC": "FA Cup",
    "FAV": "FA Vase",
    "WPS": "Wiltshire Premier Shield",
    "HCC": "Hellenic Challenge Cup",
    "HFC": "Hellenic Floodlit Cup",
}


# ---------------------------------------------------------------- loading ----
def load_rows():
    csv_path = os.path.join(DATA_DIR, "fixtures.csv")
    json_path = os.path.join(DATA_DIR, "fixtures.json")
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    raise IOError("No fixtures.csv or fixtures.json in {}".format(DATA_DIR))


# ------------------------------------------------------------ UK time -> UTC ----
def last_sunday(year, month):
    d = datetime.date(year, month, 31 if month in (3, 10) else 30)
    while d.weekday() != 6:
        d -= datetime.timedelta(days=1)
    return d


def uk_to_utc(local_dt):
    """Convert a naive UK local datetime to naive UTC (BST = last Sun Mar -> last Sun Oct)."""
    y = local_dt.year
    bst_start = datetime.datetime.combine(last_sunday(y, 3), datetime.time(1, 0))   # 01:00 UTC
    bst_end = datetime.datetime.combine(last_sunday(y, 10), datetime.time(1, 0))    # 01:00 UTC
    # Treat local time as BST if it falls inside the BST window (close enough for match times)
    as_bst = local_dt - datetime.timedelta(hours=1)
    if bst_start <= as_bst < bst_end:
        return as_bst
    return local_dt


# ------------------------------------------------------------------- ICS ----
def esc(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    """RFC 5545 line folding at 75 octets (we fold conservatively at 70 chars)."""
    out = []
    while len(line.encode("utf-8")) > 70:
        out.append(line[:70])
        line = " " + line[70:]
    out.append(line)
    return "\r\n".join(out)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def kickoff(date_obj):
    iso = date_obj.isoformat()
    if iso in KICKOFF_OVERRIDES:
        h, m = KICKOFF_OVERRIDES[iso].split(":")
        return int(h), int(m)
    if date_obj.weekday() >= 5 or iso in BANK_HOLIDAYS:   # Sat/Sun/bank holiday
        return SATURDAY_KO
    return MIDWEEK_KO


def as_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def build_event(r, stamp):
    if not r.get("date"):
        return None
    d = datetime.datetime.strptime(r["date"], "%Y-%m-%d").date()
    h, m = kickoff(d)
    start_local = datetime.datetime.combine(d, datetime.time(h, m))
    start = uk_to_utc(start_local)
    end = start + DURATION

    home = r["venue"].upper() == "H"
    opp = r["opponent"]
    comp = r["competition"]
    played = as_bool(r.get("played"))
    postponed = r.get("result_raw", "").strip().upper() in ("P-P", "PP", "POSTPONED")

    if played:
        gf, ga = r["goals_for"], r["goals_against"]
        if home:
            summary = "Bradford Town {}-{} {} ({})".format(gf, ga, opp, r["outcome"])
        else:
            summary = "{} {}-{} Bradford Town ({})".format(opp, ga, gf, r["outcome"])
    else:
        summary = ("Bradford Town v " + opp) if home else (opp + " v Bradford Town")
        if postponed:
            summary = "POSTPONED: " + summary

    desc = [COMPETITIONS.get(comp, comp), "Kick-off {:02d}:{:02d}".format(h, m)]
    if r.get("attendance"):
        desc.append("Attendance: " + str(r["attendance"]))
    if r.get("match_sponsor"):
        desc.append("Match sponsor: " + r["match_sponsor"])

    # UID doesn't include the date, so a rearranged game moves rather than duplicating
    uid = "{}-{}-{}@btfc-scraper".format(slug(comp), slug(opp), "h" if home else "a")

    lines = [
        "BEGIN:VEVENT",
        "UID:" + uid,
        "DTSTAMP:" + stamp,
        "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + end.strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + esc(summary),
        "DESCRIPTION:" + esc("\n".join(desc)),
    ]
    if home:
        lines.append("LOCATION:" + esc(HOME_GROUND))
    lines.append("END:VEVENT")
    return lines


def main():
    rows = load_rows()
    stamp = "20260101T000000Z"   # fixed, so the file only changes when fixtures change

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//btfc-scraper//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + esc(CAL_NAME),
        "X-WR-TIMEZONE:Europe/London",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    count = 0
    for r in rows:
        ev = build_event(r, stamp)
        if ev:
            lines.extend(ev)
            count += 1
    lines.append("END:VCALENDAR")

    if count == 0:
        print("No events built; leaving existing .ics alone", file=sys.stderr)
        return 1

    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(fold(l) for l in lines) + "\r\n")
    os.replace(tmp, OUT_FILE)
    print("Wrote {} events -> {}".format(count, OUT_FILE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
