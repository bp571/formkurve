"""Match detail pages: kickoff time, venue, attendance, half-time score and the
timeline of goals and cards. Two requests per match, cached under
data/raw/details/.

None of this feeds the ranking. matches.csv stays the contract; details.csv
and events.csv sit beside it, keyed by match_id, and only ever hold played
matches. Player names are decoded and kept locally for grouping, but the page
publishes aggregates only.

    python src/details.py                  # fetch what the current season lacks
    python src/details.py --season 2025/26
    python src/details.py --cached         # re-parse saved pages, no network
"""

import argparse
import csv
import logging
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

from config import CACHE_DIR, FUSSBALL_DE_BASE_URL, MATCHES_CSV, REQUEST_DELAY_SECONDS, SEASON_CURRENT
from font_decoder import decode
from scrape import _get_with_retry

logger = logging.getLogger(__name__)

DETAILS_CSV = Path(MATCHES_CSV).with_name("details.csv")
EVENTS_CSV = Path(MATCHES_CSV).with_name("events.csv")
DETAIL_CACHE = Path(CACHE_DIR) / "details"

DETAIL_FIELDS = ["match_id", "kickoff", "surface", "venue", "attendance", "ht_home", "ht_away"]
EVENT_FIELDS = ["match_id", "half", "minute", "extra", "side", "type",
                "score_home", "score_away", "player_id", "player", "note"]

# What fussball.de writes before the ground's name. Anything else is kept raw
# so it shows up in the report rather than silently becoming "other".
SURFACES = {"Kunstrasenplatz": "Kunstrasen", "Rasenplatz": "Rasen", "Hartplatz": "Hartplatz"}

RE_TIME = re.compile(r"(\d{1,2}):(\d{2})")
RE_MINUTE = re.compile(r"(\d+)\s*[’']\s*(?:\+\s*(\d+))?")
RE_HALF_RESULT = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")
RE_PLAYER_ID = re.compile(r"/(?:player-id|userid)/([A-Z0-9]+)")


def detail_url(match_id):
    return f"{FUSSBALL_DE_BASE_URL}/spiel/-/spiel/{match_id}"


def course_url(match_id):
    return f"{FUSSBALL_DE_BASE_URL}/ajax.match.course/-/mode/PAGE/spiel/{match_id}"


def fetch(match_id, use_cache=False):
    """(detail page, course fragment), from the cache when asked and present."""
    DETAIL_CACHE.mkdir(parents=True, exist_ok=True)
    pages = []
    for name, url in (("detail", detail_url(match_id)), ("course", course_url(match_id))):
        path = DETAIL_CACHE / f"{match_id}.{name}.html"
        if use_cache and path.exists():
            pages.append(path.read_text(encoding="utf-8"))
            continue
        html = _get_with_retry(url).text
        path.write_text(html, encoding="utf-8")
        pages.append(html)
        time.sleep(REQUEST_DELAY_SECONDS)
    return pages


def _decoded(node):
    """All obfuscated spans under a node, decoded and joined; undecodable
    characters become '?' so a regex on the digits still has a chance."""
    parts = []
    for span in node.select("span[data-obfuscation]"):
        text = span.get_text()
        parts.append(decode(text, span["data-obfuscation"]) or "?" * len(text))
    return " ".join(parts)


def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    row = {"kickoff": "", "surface": "", "venue": "", "attendance": "", "ht_home": "", "ht_away": ""}

    header = soup.select_one(".stage-header")
    if header:
        date_node = header.select_one(".date")
        if date_node:
            found = RE_TIME.search(_decoded(date_node))
            if found:
                row["kickoff"] = f"{int(found.group(1)):02d}:{found.group(2)}"
        location = header.select_one(".location")
        if location:
            venue = " ".join(location.get_text().split())
            row["venue"] = venue
            first = venue.split(",")[0].strip()
            row["surface"] = SURFACES.get(first, first)

    label = soup.find("span", string=re.compile(r"Zuschauer"))
    if label:
        value = label.find_next_sibling("span")
        digits = re.sub(r"\D", "", value.get_text()) if value else ""
        row["attendance"] = digits

    half = soup.select_one(".half-result")
    if half:
        found = RE_HALF_RESULT.search(half.get_text())
        if found:
            row["ht_home"], row["ht_away"] = found.groups()
    return row


def _event_type(column_event):
    # A yellow-red is two stacked <span> icons, a plain card one <i>.
    classes = {c for el in column_event.find_all(["i", "span"]) for c in el.get("class", [])}
    if column_event.select_one(".score-left"):
        return "goal"
    if "icon-substitute" in classes:
        return "sub"
    if "red-card" in classes:
        return "yellow_red" if "yellow-card" in classes else "red"
    if "yellow-card" in classes:
        return "yellow"
    return "other"


def parse_course(html, match_id):
    """Goals and cards in match order. Substitutions are skipped - nothing
    planned needs them - and anything unrecognised is kept as "other" so it
    shows up in the report."""
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for half_no, half_cls in ((1, "first-half"), (2, "second-half")):
        container = soup.select_one(f".{half_cls}")
        if container is None:
            continue
        for ev in container.select(".row-event"):
            kind = _event_type(ev.select_one(".column-event"))
            if kind == "sub":
                continue
            side = "home" if "event-left" in ev.get("class", []) else "away"
            found = RE_MINUTE.search(ev.select_one(".column-time").get_text())
            minute, extra = (found.group(1), found.group(2) or "0") if found else ("", "")

            player_col = ev.select_one(".column-player")
            link = player_col.find("a", href=RE_PLAYER_ID) if player_col else None
            player_id = RE_PLAYER_ID.search(link["href"]).group(1) if link else ""
            player = " ".join(_decoded(player_col).split()) if player_col and link else ""
            # "Strafstoßtor" or "Eigentor" next to the score; empty otherwise.
            # An own goal is listed on the side it counts for.
            info = ev.select_one(".event-info")
            note = info.get_text(strip=True) if info else ""

            score_home = score_away = ""
            if kind == "goal":
                left = ev.select_one(".score-left")
                right = ev.select_one(".score-right")
                score_home = decode(left.get_text(strip=True), left["data-obfuscation"]) or ""
                score_away = decode(right.get_text(strip=True), right["data-obfuscation"]) or ""

            events.append({
                "match_id": match_id, "half": half_no, "minute": minute, "extra": extra,
                "side": side, "type": kind, "score_home": score_home, "score_away": score_away,
                "player_id": player_id, "player": player, "note": note,
            })
    return events


def load_csv(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_csv(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def collect(season, use_cache=False):
    """Fetch and parse every played match of the season that details.csv does
    not have yet, and upsert both files. Events of a match are replaced as a
    block, so a re-parse never duplicates them."""
    played = [r for r in load_csv(MATCHES_CSV) if r["season"] == season and r["status"] == "played"]
    details = {r["match_id"]: r for r in load_csv(DETAILS_CSV)}
    events = [e for e in load_csv(EVENTS_CSV)]

    todo = [r for r in played if use_cache or r["match_id"] not in details]
    logger.info(f"{season}: {len(todo)} of {len(played)} played matches to fetch")
    for i, r in enumerate(todo, 1):
        mid = r["match_id"]
        try:
            detail_html, course_html = fetch(mid, use_cache)
        except Exception as e:  # one refused page must not lose the run
            logger.warning(f"{mid} ({r['home_team']} - {r['away_team']}): {e}")
            continue
        details[mid] = {"match_id": mid, **parse_detail(detail_html)}
        events = [e for e in events if e["match_id"] != mid] + parse_course(course_html, mid)
        logger.info(f"[{i}/{len(todo)}] md{r['matchday']} {r['home_team']} - {r['away_team']}: "
                    f"{details[mid]['surface'] or '?'}, {details[mid]['kickoff'] or '?'}, "
                    f"{sum(1 for e in events if e['match_id'] == mid)} events")
        # Written after every match: a season is half an hour of requests, and
        # an interrupted run should resume where it stopped, not start over.
        save_csv(DETAILS_CSV, sorted(details.values(), key=lambda d: d["match_id"]), DETAIL_FIELDS)
        save_csv(EVENTS_CSV, events, EVENT_FIELDS)
    logger.info(f"{len(details)} details and {len(events)} events on disk")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default=SEASON_CURRENT)
    parser.add_argument("--cached", action="store_true", help="re-parse saved pages, no network")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    collect(args.season, use_cache=args.cached)
    return 0


if __name__ == "__main__":
    sys.exit(main())
