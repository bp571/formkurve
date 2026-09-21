"""What the model expects from the next matchday - and how often that is right.

One model for the whole section: a small Poisson attack/defence fit gives each
side an expected number of goals, and the difference between the two goes
through the ordered logistic the backtest already uses to become home/draw/away
probabilities. Percentages and expected goals therefore always agree - an
Elo-based percentage next to a Poisson scoreline contradicted itself in 31% of
2025/26 matches, which on a page reads as a bug rather than as two lenses.

The calibration is fitted on the *previous* season, so it never sees a match it
scores. Measured walk-forward on 2025/26 the expected-goal difference is the
best of the candidates in src/explore_predictors.py - and still not
distinguishable from simply quoting the league's own home/draw/away rates. The
page says that outright; the forecast is a lens, not an edge.

Expected goals are a mean, not a tip: the most likely exact score is right in
4% of matches, and 2:1 alone would be the "prediction" in a third of them. The
page therefore publishes only their *sum* - open game or grind - because the
pair beside the percentages restages the same contradiction one model deeper.
"""

import csv
import math
import random
from collections import defaultdict
from functools import lru_cache

from backtest import fit_ordered_logistic, outcome_of, probabilities, rps
from config import MATCHES_CSV, SEASON_PREVIOUS

# Pseudo-matches pulling a team's scoring rates towards the league mean. Without
# it a side that won 6:0 once in September gets an absurd attack rate.
POISSON_SHRINK = 4.0

# A forecast needs something to stand on: below this many played matches the fit
# is the league average for everyone, which is not worth printing.
MIN_MATCHES = 14


def load(season, status="played"):
    with open(MATCHES_CSV, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f)
                if r["season"] == season and r["status"] == status]


def goals(row):
    return int(row["home_goals"]), int(row["away_goals"])


def poisson_model(rows, shrink=POISSON_SHRINK):
    """Attack and defence rates per team, plus the league's home factor.

    Iterative scaling: each team's rate is the goals it actually got divided by
    the goals the current fit expects it to have got, with `shrink` pseudo-
    matches of league average mixed in so early-season rates stay sane.
    `mu` is goals per team per match, `home_factor` what playing at home
    multiplies that by.
    """
    teams = sorted({r[s] for r in rows for s in ("home_team", "away_team")})
    scored = [goals(r) for r in rows]
    mu = sum(h + a for h, a in scored) / (2 * len(rows))
    home_factor = (sum(h for h, _ in scored) / len(rows)) / mu

    atk = dict.fromkeys(teams, 1.0)
    dfn = dict.fromkeys(teams, 1.0)
    for _ in range(30):
        gf, ga = defaultdict(float), defaultdict(float)
        e_atk, e_dfn = defaultdict(float), defaultdict(float)
        for r, (hg, ag) in zip(rows, scored):
            h, a = r["home_team"], r["away_team"]
            gf[h] += hg; ga[h] += ag
            gf[a] += ag; ga[a] += hg
            e_atk[h] += mu * home_factor * dfn[a]
            e_atk[a] += mu * dfn[h]
            e_dfn[a] += mu * home_factor * atk[h]
            e_dfn[h] += mu * atk[a]
        for t in teams:
            atk[t] = (gf[t] + shrink * mu) / (e_atk[t] + shrink * mu)
            dfn[t] = (ga[t] + shrink * mu) / (e_dfn[t] + shrink * mu)
    return mu, home_factor, atk, dfn


def expected_goals(model, home, away):
    mu, home_factor, atk, dfn = model
    return (mu * home_factor * atk.get(home, 1.0) * dfn.get(away, 1.0),
            mu * atk.get(away, 1.0) * dfn.get(home, 1.0))


def walk_forward(rows):
    """(row, expected-goal difference) using only the matchdays before it.

    Refit per matchday rather than per match: within a matchday the results are
    simultaneous anyway, and it keeps the number of fits to one per matchday.
    """
    rows = sorted(rows, key=lambda r: (r["date"], int(r["matchday"])))
    out = []
    for matchday in sorted({int(r["matchday"]) for r in rows}):
        earlier = [r for r in rows if int(r["matchday"]) < matchday]
        if len(earlier) < MIN_MATCHES:
            continue
        model = poisson_model(earlier)
        for r in rows:
            if int(r["matchday"]) == matchday:
                home_xg, away_xg = expected_goals(model, r["home_team"], r["away_team"])
                out.append((r, home_xg - away_xg))
    return out


@lru_cache(maxsize=None)
def calibration(season=SEASON_PREVIOUS):
    """Expected-goal difference -> H/D/A, fitted once on a completed season."""
    samples = [(diff, outcome_of(*goals(r))) for r, diff in walk_forward(load(season))]
    if not samples:
        # Without them the fit returns its starting values and every match
        # reads 31/38/31 - a stale season id or a missing matches.csv must
        # fail here, not on the page.
        raise ValueError(f"no played matches for {season} in {MATCHES_CSV}")
    return fit_ordered_logistic(samples)


def next_matchday(scheduled):
    """The next matchday, not the next fixture: a postponed match keeps its own
    matchday, so the lowest scheduled one can be a catch-up game from weeks ago.
    Take the earliest matchday that still has most of its fixtures to play."""
    by_matchday = defaultdict(list)
    for r in scheduled:
        by_matchday[int(r["matchday"])].append(r)
    full = [md for md, rs in by_matchday.items() if len(rs) >= 4]
    return (min(full), by_matchday[min(full)]) if full else (None, [])


def forecast(played, fixtures):
    """Probabilities and expected goals per fixture, in kick-off order."""
    if len(played) < MIN_MATCHES or not fixtures:
        return []
    params = calibration()
    model = poisson_model(played)

    out = []
    for f in sorted(fixtures, key=lambda r: (r["date"], r["home_team"])):
        home_xg, away_xg = expected_goals(model, f["home_team"], f["away_team"])
        p_away, p_draw, p_home = probabilities(home_xg - away_xg, params)
        out.append({"date": f["date"], "home": f["home_team"], "away": f["away_team"],
                    "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
                    "xg_home": home_xg, "xg_away": away_xg})
    return out


def track_record(rows):
    """How this forecast would have done on the matches already played.

    Recomputed from matches.csv rather than stored: the forecast is a pure
    function of the results before it, so replaying it reproduces exactly what
    the page showed at the time. The yardstick is the constant league base rate
    - what anyone can predict without a model at all.
    """
    params = calibration()
    base = probabilities(0.0, params)

    # Both tip the most likely outcome, whatever its size - for the base rate
    # that is the home win every time.
    hits, base_hits, model_rps, base_rps = 0, 0, [], []
    for r, diff in walk_forward(rows):
        outcome = outcome_of(*goals(r))
        p = probabilities(diff, params)
        hits += max(range(3), key=p.__getitem__) == outcome
        base_hits += max(range(3), key=base.__getitem__) == outcome
        model_rps.append(rps(p, outcome))
        base_rps.append(rps(base, outcome))

    if not model_rps:
        return None
    n = len(model_rps)
    return {"n": n, "hits": hits, "base_hits": base_hits,
            "rps": sum(model_rps) / n, "base_rps": sum(base_rps) / n}


SIM_RUNS = 4000
SIM_SEED = 1


def simulate_season(played, scheduled, runs=SIM_RUNS, seed=SIM_SEED):
    """Monte Carlo over remaining fixtures. Returns {team: {"pts": current,
    "exp_pts": mean final, "p_first": float, "p_bottom2": float}} or None if
    nothing is scheduled or not enough matches played."""
    if not scheduled or len(played) < MIN_MATCHES:
        return None

    # Current state: points, goal difference, goals scored per team
    teams = {r[side] for r in played for side in ("home_team", "away_team")}
    current_state = {team: {"pts": 0, "gd": 0, "gf": 0} for team in teams}
    for r in played:
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        h, a = r["home_team"], r["away_team"]
        current_state[h]["gf"] += hg
        current_state[a]["gf"] += ag
        current_state[h]["gd"] += hg - ag
        current_state[a]["gd"] += ag - hg
        if hg > ag:
            current_state[h]["pts"] += 3
        elif hg < ag:
            current_state[a]["pts"] += 3
        else:
            current_state[h]["pts"] += 1
            current_state[a]["pts"] += 1

    model = poisson_model(played)
    rng = random.Random(seed)

    # Poisson draw using Knuth algorithm
    def poisson(lam):
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while p > L:
            k += 1
            p *= rng.random()
        return k - 1

    # Simulate
    first_count = {team: 0 for team in teams}
    bottom2_count = {team: 0 for team in teams}
    final_pts = {team: [] for team in teams}

    for _ in range(runs):
        state = {team: current_state[team].copy() for team in teams}

        for s in scheduled:
            h, a = s["home_team"], s["away_team"]
            hg = poisson(expected_goals(model, h, a)[0])
            ag = poisson(expected_goals(model, h, a)[1])

            if hg > ag:
                state[h]["pts"] += 3
            elif hg < ag:
                state[a]["pts"] += 3
            else:
                state[h]["pts"] += 1
                state[a]["pts"] += 1
            state[h]["gf"] += hg
            state[a]["gf"] += ag
            state[h]["gd"] += hg - ag
            state[a]["gd"] += ag - hg

        # Rank using same tie-break as official_positions: pts, gd, gf, then name
        ranked = sorted(teams,
                       key=lambda t: (state[t]["pts"], state[t]["gd"], state[t]["gf"], t),
                       reverse=True)

        first_count[ranked[0]] += 1
        bottom2_count[ranked[12]] += 1
        bottom2_count[ranked[13]] += 1
        for team in teams:
            final_pts[team].append(state[team]["pts"])

    # Sanity checks
    first_sum = sum(first_count.values())
    bottom2_sum = sum(bottom2_count.values())
    if abs(first_sum - runs) > 1e-6 or abs(bottom2_sum - 2 * runs) > 1e-6:
        raise ValueError(f"Simulation sanity check failed: first_sum={first_sum}, bottom2_sum={bottom2_sum}")

    return {
        team: {
            "pts": current_state[team]["pts"],
            "exp_pts": sum(final_pts[team]) / runs,
            "p_first": first_count[team] / runs,
            "p_bottom2": bottom2_count[team] / runs,
        }
        for team in teams
    }


if __name__ == "__main__":
    from config import SEASON_CURRENT

    played = load(SEASON_CURRENT)
    matchday, fixtures = next_matchday(load(SEASON_CURRENT, "scheduled"))
    print(f"Spieltag {matchday}\n")
    for f in forecast(played, fixtures):
        print(f"{f['date']}  {f['home']:<32}{f['away']:<32}"
              f"{f['p_home']:>5.0%}{f['p_draw']:>5.0%}{f['p_away']:>5.0%}"
              f"   {f['xg_home']:.1f}:{f['xg_away']:.1f}")
    record = track_record(played)
    if record:
        print(f"\nBisher: {record['hits']}/{record['n']} richtig, immer Heimsieg {record['base_hits']}/{record['n']}, "
              f"RPS {record['rps']:.4f} gegen {record['base_rps']:.4f} (Basisrate)")
