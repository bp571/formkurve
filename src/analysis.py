"""Two questions the detail pages can answer and the results alone cannot.

Surface effect: does the pitch (grass, artificial, cinder) change how matches
go - goals, home win rate, blowouts. League-wide only; the CLAUDE.md finding
that no club has a home advantage of its own stands, this is about the
ground, not the club.

Comebacks and dropped leads: from the goal timeline, which teams take points
from matches they were behind in, and which give away matches they led.
Descriptive, per team, with the counts beside the rates.

    python src/analysis.py                 # current season
    python src/analysis.py --season 2025/26
    python src/analysis.py --all           # both seasons pooled for the surface table
"""

import argparse
import sys
from collections import defaultdict

from config import MATCHES_CSV, SCALE, SEASON_CURRENT, STAFFEL_IDS
from details import DETAILS_CSV, EVENTS_CSV, load_csv
from rating import EloRating


def played(seasons):
    return [r for r in load_csv(MATCHES_CSV)
            if r["season"] in seasons and r["status"] == "played"]


def points(goals_for, goals_against):
    return 3 if goals_for > goals_against else (1 if goals_for == goals_against else 0)


def season_ratings(rows):
    """Season-end Elo, replayed in match order - the same replay report.py
    runs, kept here so report can import this module without a cycle."""
    elo = EloRating()
    elo.initialize_teams(sorted({r[side] for r in rows for side in ("home_team", "away_team")}))
    for r in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        elo.update_from_match(r["home_team"], r["away_team"],
                              int(r["home_goals"]), int(r["away_goals"]), int(r["matchday"]))
    return elo.get_ratings()


def surface_effect(rows, details):
    """Per surface: matches, goals per match, H/D/A shares, share of 3+ goal
    margins, and the home advantage left once team strength is taken out.

    The raw home win rate is confounded: the clubs with artificial turf are
    not the same strength as the clubs on grass. So each match also gets the
    home side's expected score from the two season-end Elo ratings *without*
    the home-field term, and "hfa" is actual minus that - the part of the
    home result the pairing does not explain. The league line is the
    reference, and the difference between surfaces carries a standard error
    of roughly 0.07 at these counts.
    """
    by_id = {d["match_id"]: d for d in details}
    rows = [r for r in rows if r["match_id"] in by_id]
    if not rows:
        return {}
    expected = {}
    for season in {r["season"] for r in rows}:
        season_rows = [r for r in rows if r["season"] == season]
        ratings = season_ratings(season_rows)
        for r in season_rows:
            gap = ratings[r["home_team"]] - ratings[r["away_team"]]
            expected[r["match_id"]] = 1 / (1 + 10 ** (-gap / SCALE))

    buckets = defaultdict(list)
    for r in rows:
        d = by_id.get(r["match_id"])
        if d:
            buckets[d["surface"]].append(r)
    buckets["alle"] = rows

    out = {}
    for surface, ms in buckets.items():
        n = len(ms)
        hg = [int(r["home_goals"]) for r in ms]
        ag = [int(r["away_goals"]) for r in ms]
        actual = [1.0 if h > a else 0.5 if h == a else 0.0 for h, a in zip(hg, ag)]
        out[surface] = {
            "n": n,
            "goals": sum(hg + ag) / n,
            "home": sum(h > a for h, a in zip(hg, ag)) / n,
            "draw": sum(h == a for h, a in zip(hg, ag)) / n,
            "away": sum(h < a for h, a in zip(hg, ag)) / n,
            "blowout": sum(abs(h - a) >= 3 for h, a in zip(hg, ag)) / n,
            "zero_zero": sum(h == 0 and a == 0 for h, a in zip(hg, ag)) / n,
            # In percentage points: +7 reads as "seven wins per hundred home
            # matches more than the pairing alone would give".
            "home_bonus": 100 * (sum(actual) - sum(expected[r["match_id"]] for r in ms)) / n,
        }
    return out


def surface_splits(rows, details):
    """The check behind the surface table: is the bonus the pitch or the club?

    Two cuts of the same residual (home score minus Elo expectation without
    HFA). By the *visitor's* usual surface: if turf only hurt visitors used to
    grass, grass clubs would lose most on turf - they do not, turf clubs do.
    Within one club at home on both surfaces: the pitch, with the club held
    fixed. Every cell sits at one to two standard errors; the table's "hint,
    not finding" is what this earns.
    """
    by_id = {d["match_id"]: d for d in details}
    rows = [r for r in rows if r["match_id"] in by_id
            and by_id[r["match_id"]]["surface"] != "Hartplatz"]
    resid, usual = {}, {}
    for season in {r["season"] for r in rows}:
        season_rows = [r for r in rows if r["season"] == season]
        ratings = season_ratings(season_rows)
        homes = defaultdict(lambda: defaultdict(int))
        for r in season_rows:
            gap = ratings[r["home_team"]] - ratings[r["away_team"]]
            hg, ag = int(r["home_goals"]), int(r["away_goals"])
            actual = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
            resid[r["match_id"]] = actual - 1 / (1 + 10 ** (-gap / SCALE))
            homes[r["home_team"]][by_id[r["match_id"]]["surface"]] += 1
        for team, counts in homes.items():
            usual[(season, team)] = max(counts, key=counts.get)

    visitor = defaultdict(list)
    within = defaultdict(list)
    for r in rows:
        surface = by_id[r["match_id"]]["surface"]
        visitor[(surface, usual[(r["season"], r["away_team"])])].append(resid[r["match_id"]])
        # Only clubs that hosted on both surfaces in that season count here.
        season_home = {s for s, m in
                       ((by_id[x["match_id"]]["surface"], x) for x in rows
                        if x["season"] == r["season"] and x["home_team"] == r["home_team"])}
        if len(season_home) == 2:
            within[surface].append(resid[r["match_id"]])
    return visitor, within


def _cell(xs):
    n = len(xs)
    mean = sum(xs) / n
    se = (sum((x - mean) ** 2 for x in xs) / (n - 1) / n) ** 0.5 if n > 1 else float("nan")
    return f"n={n:>3}  {100 * mean:+5.1f} ± {100 * se:4.1f}"


def print_splits(visitor, within):
    print("Heimbonus nach Belag des Spiels x gewohntem Belag des Gastes")
    for (surface, guest), xs in sorted(visitor.items()):
        print(f"  auf {surface:<11} Gast gewohnt {guest:<11} {_cell(xs)}")
    print("Heimbonus derselben Vereine daheim auf beiden Belägen")
    for surface, xs in sorted(within.items()):
        print(f"  auf {surface:<11} {_cell(xs)}")


def goal_timeline(events):
    """match_id -> goals in order, each (score_home, score_away) after it."""
    goals = defaultdict(list)
    for e in events:
        if e["type"] == "goal":
            goals[e["match_id"]].append((int(e["score_home"]), int(e["score_away"])))
    # The running score orders the goals, whatever the minutes say.
    return {mid: sorted(gs, key=lambda s: s[0] + s[1]) for mid, gs in goals.items()}


def comebacks(rows, events):
    """Per team: matches trailed in and the points taken from them, matches
    led in and the points given away in them. Plus the league's own numbers
    for the team scoring first."""
    timeline = goal_timeline(events)
    teams = defaultdict(lambda: {
        "matches": 0, "trailed": 0, "pts_after_trailing": 0, "won_after_trailing": 0,
        "led": 0, "pts_dropped_leading": 0, "lost_after_leading": 0,
    })
    first = {"n": 0, "win": 0, "draw": 0, "loss": 0}

    for r in rows:
        if r["match_id"] not in timeline and int(r["home_goals"]) + int(r["away_goals"]) > 0:
            continue  # no timeline for this match
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        scores = timeline.get(r["match_id"], [])
        home_led = any(h > a for h, a in scores)
        away_led = any(h < a for h, a in scores)
        for team, own, opp, led, trailed in (
            (r["home_team"], hg, ag, home_led, away_led),
            (r["away_team"], ag, hg, away_led, home_led),
        ):
            t = teams[team]
            p = points(own, opp)
            t["matches"] += 1
            if trailed:
                t["trailed"] += 1
                t["pts_after_trailing"] += p
                t["won_after_trailing"] += p == 3
            if led:
                t["led"] += 1
                t["pts_dropped_leading"] += 3 - p
                t["lost_after_leading"] += p == 0
        if scores:
            first["n"] += 1
            h, a = scores[0]
            scorer_won = (hg > ag) if h > a else (ag > hg)
            if hg == ag:
                first["draw"] += 1
            elif scorer_won:
                first["win"] += 1
            else:
                first["loss"] += 1
    return dict(teams), first


# Floors for the two comeback markers: below one win's worth of points there
# is nothing to single out, and the page must not badge a coin flip.
MIN_OUTLIER_POINTS = 4


def outliers(teams):
    """The one team that took the most points from behind and the one that
    gave away the most from in front - each only above the floor. Ties go to
    the team that needed fewer matches to get there."""
    if not teams:
        return {"comeback": None, "dropped": None}
    best = max(teams.items(), key=lambda kv: (kv[1]["pts_after_trailing"], -kv[1]["trailed"]))
    worst = max(teams.items(), key=lambda kv: (kv[1]["pts_dropped_leading"], -kv[1]["led"]))
    return {
        "comeback": best if best[1]["pts_after_trailing"] >= MIN_OUTLIER_POINTS else None,
        "dropped": worst if worst[1]["pts_dropped_leading"] >= MIN_OUTLIER_POINTS else None,
    }


def page_data(season):
    """What the page will show: the surface table over every season on file
    (the current one alone is too thin for it), the two comeback outliers and
    the first-goal rates for the current season only."""
    details = load_csv(DETAILS_CSV)
    events = load_csv(EVENTS_CSV)
    surface = surface_effect(played(set(STAFFEL_IDS)), details)
    teams, first = comebacks(played({season}), events)
    return surface, outliers(teams), first


def print_surface(table):
    print(f"{'Belag':<12}{'n':>5}{'Tore/Spiel':>12}{'Heim':>7}{'Remis':>7}{'Ausw.':>7}{'3+ Diff':>9}{'0:0':>6}{'Heimbonus':>11}")
    for surface, s in sorted(table.items(), key=lambda kv: (kv[0] == "alle", -kv[1]["n"])):
        print(f"{surface:<12}{s['n']:>5}{s['goals']:>12.2f}{s['home']:>7.0%}{s['draw']:>7.0%}"
              f"{s['away']:>7.0%}{s['blowout']:>9.0%}{s['zero_zero']:>6.0%}{s['home_bonus']:>+11.0f}")
    print("Heimbonus = Prozentpunkte, die der Gastgeber besser abschneidet als nach Stärke beider Teams "
          "erwartet (Sieg 100, Remis 50); Unterschied zwischen Belägen hat se ~7")


def print_comebacks(teams, first):
    print(f"{'Team':<32}{'Sp':>4}{'Rückst.':>8}{'Pkt':>5}{'Siege':>6}   {'Führung':>8}{'verspielt':>10}{'verloren':>9}")
    for team, t in sorted(teams.items(), key=lambda kv: -kv[1]["pts_after_trailing"]):
        print(f"{team:<32}{t['matches']:>4}{t['trailed']:>8}{t['pts_after_trailing']:>5}"
              f"{t['won_after_trailing']:>6}   {t['led']:>8}{t['pts_dropped_leading']:>10}"
              f"{t['lost_after_leading']:>9}")
    n = first["n"]
    if n:
        print(f"\nWer das erste Tor schießt ({n} Spiele mit Toren): gewinnt {first['win'] / n:.0%}, "
              f"remis {first['draw'] / n:.0%}, verliert {first['loss'] / n:.0%}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default=SEASON_CURRENT, choices=sorted(STAFFEL_IDS))
    parser.add_argument("--all", action="store_true", help="pool every season")
    args = parser.parse_args()
    seasons = set(STAFFEL_IDS) if args.all else {args.season}

    rows = played(seasons)
    details = load_csv(DETAILS_CSV)
    events = load_csv(EVENTS_CSV)

    print(f"Belag, {' + '.join(sorted(seasons))}")
    print_surface(surface_effect(rows, details))
    print("\nPlatz oder Verein?")
    print_splits(*surface_splits(rows, details))
    print(f"\nRückstände und Führungen, {' + '.join(sorted(seasons))}")
    print("Rückst. = Spiele mit Rückstand, Pkt/Siege = daraus geholt; "
          "Führung = Spiele mit Führung, verspielt = darin liegen gelassene Punkte")
    teams, first = comebacks(rows, events)
    print_comebacks(teams, first)

    marks = outliers(teams)
    print(f"\nAusreißer für die Seite (Floor: {MIN_OUTLIER_POINTS} Punkte)")
    for key, label, pts, n in (("comeback", "Punkte nach Rückstand", "pts_after_trailing", "trailed"),
                               ("dropped", "aus Führung verspielt", "pts_dropped_leading", "led")):
        if marks[key]:
            team, t = marks[key]
            print(f"  {label}: {team} - {t[pts]} Punkte in {t[n]} Spielen")
        else:
            print(f"  {label}: keiner über dem Floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
