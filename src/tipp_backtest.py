"""How would the score tip have done in a Kicktipp-style prediction game?

Kicktipp's standard scoring: exact score 4 points, correct goal difference 3,
correct tendency (home / draw / away) 2, otherwise 0. Walk-forward on the
current season: every matchday is tipped from the matches before it, with the
same fit and calibration the page would have used. Compared against constant
tips and against the unconstrained most-likely score.

    python src/tipp_backtest.py
"""

import math
from collections import defaultdict

from backtest import outcome_of, probabilities
from config import SEASON_CURRENT
from predict import (MIN_MATCHES, _current_calibration, _fit, _previous_samples,
                     expected_goals, goals, load, poisson_model, tip)


def kicktipp_points(tipped, actual):
    """Kicktipp standard: 4 exact, 3 goal difference, 2 tendency, else 0."""
    if tipped == actual:
        return 4
    if outcome_of(*tipped) != outcome_of(*actual):
        return 0
    return 3 if tipped[0] - tipped[1] == actual[0] - actual[1] else 2


def best_score(home_xg, away_xg, max_goals=8):
    def pmf(k, lam):
        return math.exp(-lam) * lam ** k / math.factorial(k)
    return max(((h, a) for h in range(max_goals + 1) for a in range(max_goals + 1)),
               key=lambda s: pmf(s[0], home_xg) * pmf(s[1], away_xg))


def main():
    rows = sorted(load(SEASON_CURRENT), key=lambda r: (r["date"], int(r["matchday"])))
    strategies = {
        "Modelltipp (wie auf der Seite)": None,
        "Wahrscheinlichstes Einzelergebnis": lambda hx, ax, o: best_score(hx, ax),
        "Erwartete Tore gerundet": lambda hx, ax, o: (round(hx), round(ax)),
        "Immer 2:1": lambda hx, ax, o: (2, 1),
        "Immer 1:0": lambda hx, ax, o: (1, 0),
        "Immer 1:1": lambda hx, ax, o: (1, 1),
    }
    points = defaultdict(list)
    exact = defaultdict(int)

    for md in sorted({int(r["matchday"]) for r in rows}):
        earlier = [r for r in rows if int(r["matchday"]) < md]
        if len(earlier) < MIN_MATCHES:
            continue
        model = poisson_model(earlier, asof=md)
        # Same calibration as the page: previous season plus this one before `md`.
        params = _current_calibration(earlier)
        for r in (r for r in rows if int(r["matchday"]) == md):
            hx, ax = expected_goals(model, r["home_team"], r["away_team"])
            p = probabilities(hx - ax, params)
            outcome = max(range(3), key=p.__getitem__)
            actual = goals(r)
            for name, fn in strategies.items():
                t = tip(hx, ax, outcome) if fn is None else fn(hx, ax, outcome)
                pts = kicktipp_points(t, actual)
                points[name].append(pts)
                exact[name] += pts == 4

    n = len(points["Modelltipp (wie auf der Seite)"])
    print(f"{SEASON_CURRENT}, {n} Spiele ab dem Zeitpunkt mit genug Daten\n")
    print(f"{'Strategie':36}{'Punkte':>8}{'pro Spiel':>11}{'exakt':>7}{'Diff.':>7}{'Tendenz':>9}{'0':>5}")
    for name, pts in sorted(points.items(), key=lambda kv: -sum(kv[1])):
        c = {k: pts.count(k) for k in (4, 3, 2, 0)}
        print(f"{name:36}{sum(pts):>8}{sum(pts) / n:>11.2f}{c[4]:>7}{c[3]:>7}{c[2]:>9}{c[0]:>5}")


if __name__ == "__main__":
    main()
