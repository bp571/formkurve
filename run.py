#!/usr/bin/env python
"""Entrypoint: scrape -> parse -> rate. Run once after each matchday."""

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import (  # noqa: E402
    FORM_WINDOW,
    MANUAL_OVERRIDES_CSV,
    MATCHES_CSV,
    SEASON_CURRENT,
    STAFFEL_IDS,
)
from details import collect as collect_details  # noqa: E402
from parse import collect_season, parse_matchday, save_matches_csv, validate  # noqa: E402
from report import build_table, write_season  # noqa: E402
from scrape import fetch_matchday  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def apply_overrides(matches, season: str):
    """Merge hand-entered rows for matches fussball.de cannot serve.
    Same columns as matches.csv; a row wins over the scraped one."""
    path = Path(MANUAL_OVERRIDES_CSV)
    if not path.exists():
        return matches

    by_id = {m["match_id"]: m for m in matches}
    added = 0
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["season"] != season:
                continue
            row["matchday"] = int(row["matchday"])
            if row["status"] == "played":
                row["home_goals"] = int(row["home_goals"])
                row["away_goals"] = int(row["away_goals"])
            by_id[row["match_id"]] = row
            added += 1

    if added:
        logger.info(f"Applied {added} manual override(s) from {path.name}")
    return sorted(by_id.values(), key=lambda m: (m["matchday"], m["date"]))


def load_season(season: str):
    with open(MATCHES_CSV, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["season"] == season]


def rank(season: str):
    """The table the page shows, sorted by current form.

    Built by report.build_table() rather than replayed a second time here, so the
    terminal and docs/index.html cannot drift apart - they are the same numbers in
    the same order.
    """
    rows = [r for r in load_season(season) if r["status"] == "played"]
    if not rows:
        return []
    table, _ = build_table(rows)
    return table


def find_scrape_bound(season: str, existing):
    """The last matchday to scrape this run: the last known played one plus
    one for the forecast, walked forward past any matchday that turns out to
    already be complete too - a missed run can let more than one matchday go
    by, and a fixed +1 would silently leave the extra one as 'scheduled'.
    Capped at the season length already known locally."""
    played = [int(r["matchday"]) for r in existing if r["status"] == "played"]
    if not played:
        return None

    total = max(int(r["matchday"]) for r in existing)
    bound = min(max(played) + 1, total)
    while bound < total:
        found = parse_matchday(fetch_matchday(season, bound), season, bound)
        if not found or any(m["status"] == "scheduled" for m in found):
            break
        bound += 1
    return bound


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default=SEASON_CURRENT, choices=sorted(STAFFEL_IDS))
    parser.add_argument(
        "--cached",
        action="store_true",
        help="parse the saved pages in data/raw instead of re-fetching",
    )
    args = parser.parse_args()

    existing = load_season(args.season) if Path(MATCHES_CSV).exists() else []
    max_matchday = None if args.cached else find_scrape_bound(args.season, existing)

    matches = collect_season(args.season, use_cache=args.cached, max_matchday=max_matchday)

    if max_matchday is not None:
        fetched_ids = {m["match_id"] for m in matches}
        tail = [
            dict(r, matchday=int(r["matchday"]))
            for r in existing
            if int(r["matchday"]) > max_matchday and r["match_id"] not in fetched_ids
        ]
        matches += tail

    matches = apply_overrides(matches, args.season)
    validate(matches, args.season)
    save_matches_csv(matches, MATCHES_CSV)
    # Detail pages for the matches details.csv lacks - seven per matchday.
    # --cached means no network, and the details on disk simply stay as they are.
    if not args.cached:
        collect_details(args.season)

    table = rank(args.season)
    if not table:
        logger.info(f"No played matches yet in {args.season} - nothing to rank.")
        return 0

    print(f"\nFormtabelle {args.season}  (letzte {FORM_WINDOW} Spieltage)")
    print(f"{'#':>3}  {'Team':<32}{'Form':>7}{'Saison':>8}{'Sp':>4}{'Tab':>5}")
    for i, t in enumerate(table, 1):
        # Same chip as the page: how far the official table sits from this rank.
        diff = t["position"] - i
        note = f"  {diff:+d}" if diff else ""
        print(f"{i:>3}. {t['team']:<32}{t['form']:>7.1f}{t['power']:>8.1f}"
              f"{t['matches']:>4}{t['position']:>5}{note}")

    all_rows = load_season(args.season)
    logger.info(f"Wrote {write_season(args.season, all_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
