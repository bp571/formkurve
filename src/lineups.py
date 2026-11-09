"""Match line-ups and substitutions: who started, who sat on the bench, who came
on and off and when. One extra request per match (the line-up fragment; the
substitutions are already in the cached course fragment of details.py).

Like details.csv and events.csv these files sit beside the contract and are
never read by the ranking. Player ids only, no names: the page publishes
aggregates, and ids are enough to follow a player over matches.

    python src/lineups.py                  # fetch what the current season lacks
    python src/lineups.py --season 2025/26
    python src/lineups.py --cached         # re-parse saved pages, no network
"""

import argparse
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

from config import CACHE_DIR, FUSSBALL_DE_BASE_URL, MATCHES_CSV, REQUEST_DELAY_SECONDS, SEASON_CURRENT
from details import DETAIL_CACHE, RE_MINUTE, RE_PLAYER_ID, _decoded, load_csv, save_csv
from scrape import _get_with_retry

logger = logging.getLogger(__name__)

LINEUPS_CSV = Path(MATCHES_CSV).with_name("lineups.csv")
SUBS_CSV = Path(MATCHES_CSV).with_name("subs.csv")
LINEUP_CACHE = Path(CACHE_DIR) / "lineups"

LINEUP_FIELDS = ["match_id", "side", "player_id", "player", "role", "number", "mark"]
SUB_FIELDS = ["match_id", "half", "minute", "extra", "side", "player_in", "player_out"]


def lineup_url(match_id):
    return f"{FUSSBALL_DE_BASE_URL}/ajax.match.lineup/-/mode/PAGE/spiel/{match_id}"


def fetch(match_id, use_cache=False):
    LINEUP_CACHE.mkdir(parents=True, exist_ok=True)
    path = LINEUP_CACHE / f"{match_id}.lineup.html"
    if use_cache and path.exists():
        return path.read_text(encoding="utf-8")
    html = _get_with_retry(lineup_url(match_id)).text
    path.write_text(html, encoding="utf-8")
    time.sleep(REQUEST_DELAY_SECONDS)
    return html


def parse_lineup(html, match_id):
    """Starters and bench per side. The trainer block has no player-id links of
    the same kind and is skipped by only reading .starting and .substitutes."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for role, selector in (("start", ".starting"), ("bench", ".substitutes")):
        block = soup.select_one(selector)
        if block is None:
            continue
        for a in block.select("a.player-wrapper"):
            found = RE_PLAYER_ID.search(a.get("href", ""))
            if not found:
                continue
            number = a.select_one(".player-number")
            mark = a.select_one(".captain")
            name = a.select_one(".player-name")
            rows.append({
                "match_id": match_id,
                "side": "home" if "home" in a.get("class", []) else "away",
                "player_id": found.group(1), "role": role,
                # Decoded and kept locally for reading the analysis; never published.
                "player": " ".join(_decoded(name).split()) if name else "",
                "number": number.get_text(strip=True) if number else "",
                "mark": mark.get_text(strip=True) if mark else "",
            })
    return rows


def parse_subs(course_html, match_id):
    """Substitutions from the course fragment: who came on for whom, and when."""
    soup = BeautifulSoup(course_html, "html.parser")
    subs = []
    for half_no, half_cls in ((1, "first-half"), (2, "second-half")):
        container = soup.select_one(f".{half_cls}")
        if container is None:
            continue
        for ev in container.select(".row-event"):
            if ev.select_one(".icon-substitute") is None:
                continue
            ids = [RE_PLAYER_ID.search(a["href"]).group(1)
                   for a in ev.select(".column-player a[href]") if RE_PLAYER_ID.search(a["href"])]
            found = RE_MINUTE.search(ev.select_one(".column-time").get_text())
            minute, extra = (found.group(1), found.group(2) or "0") if found else ("", "")
            subs.append({
                "match_id": match_id, "half": half_no, "minute": minute, "extra": extra,
                "side": "home" if "event-left" in ev.get("class", []) else "away",
                "player_in": ids[0] if ids else "", "player_out": ids[1] if len(ids) > 1 else "",
            })
    return subs


# A regular started at least this share of his team's matches this season, counted
# from at least MIN_PRIOR earlier matches (so from matchday 5 on). Counted over the
# current season only: a new signing is a regular as soon as he plays like one and a
# player who left drops out by himself. Over both seasons, leavers kept "missing"
# every match and signings took half a season to count.
REGULAR_SHARE = 0.6
MIN_PRIOR = 4
FIRST_MATCHDAY = 5
# Fewer missing regulars than this is the normal state of an amateur side (2.3 on
# average); from here the results fall behind.
REPORT_FROM = 3


def absent_regulars(rows):
    """{match_id: {"home": n, "away": n}}: how many of a team's regulars were not in
    the starting eleven. Only for matches from matchday FIRST_MATCHDAY with a line-up
    on file; empty without lineups.csv. A function of the matches before each one."""
    starters = defaultdict(set)
    for r in load_csv(LINEUPS_CSV):
        if r["role"] == "start":
            starters[(r["match_id"], r["side"])].add(r["player_id"])
    out = {}
    history = defaultdict(list)  # (season, team) -> starting elevens, in match order
    for m in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        mid = m["match_id"]
        if not all(len(starters[(mid, s)]) >= 7 for s in ("home", "away")):
            continue  # a line-up that did not load is unknown, not "everyone missing"
        for side, team in (("home", m["home_team"]), ("away", m["away_team"])):
            past = history[(m["season"], team)]
            if len(past) >= MIN_PRIOR and int(m["matchday"]) >= FIRST_MATCHDAY:
                seen = Counter(p for eleven in past for p in eleven)
                regulars = {p for p, c in seen.items() if c / len(past) >= REGULAR_SHARE}
                out.setdefault(mid, {})[side] = len(regulars - starters[(mid, side)])
            past.append(starters[(mid, side)])
    return out


def collect(season, use_cache=False):
    played = [r for r in load_csv(MATCHES_CSV) if r["season"] == season and r["status"] == "played"]
    lineups = load_csv(LINEUPS_CSV)
    subs = load_csv(SUBS_CSV)
    have = {r["match_id"] for r in lineups}

    todo = [r for r in played if use_cache or r["match_id"] not in have]
    logger.info(f"{season}: {len(todo)} of {len(played)} played matches to fetch")
    for i, r in enumerate(todo, 1):
        mid = r["match_id"]
        try:
            lineup = parse_lineup(fetch(mid, use_cache), mid)
            course = (DETAIL_CACHE / f"{mid}.course.html").read_text(encoding="utf-8")
            match_subs = parse_subs(course, mid)
        except Exception as e:  # one refused page must not lose the run
            logger.warning(f"{mid} ({r['home_team']} - {r['away_team']}): {e}")
            continue
        lineups = [x for x in lineups if x["match_id"] != mid] + lineup
        subs = [x for x in subs if x["match_id"] != mid] + match_subs
        if use_cache:
            time.sleep(1)  # parsing downloads the obfuscation font: keep it gentle
        logger.info(f"[{i}/{len(todo)}] md{r['matchday']} {r['home_team']} - {r['away_team']}: "
                    f"{len(lineup)} players, {len(match_subs)} subs")
        save_csv(LINEUPS_CSV, lineups, LINEUP_FIELDS)
        save_csv(SUBS_CSV, subs, SUB_FIELDS)
    logger.info(f"{len(lineups)} line-up rows and {len(subs)} substitutions on disk")


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
