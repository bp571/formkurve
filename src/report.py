"""Render the ranking as a single self-contained HTML page (docs/index.html).

Only derived numbers are published - power scores, records, goal difference -
never the scraped match rows themselves; fussball.de is linked as the source.
"""

import base64
import html
import math
import os
from collections import defaultdict
from datetime import date

from config import (
    FONT_DIR,
    FORM_WINDOW,
    LOGO_DIR,
    MATCHES_CSV,
    POWER_SCALE_DIVISOR,
    R0,
    SEASON_CURRENT,
    SEASON_PREVIOUS,
    STAFFEL_IDS,
    STAFFEL_NAME,
    team_slug,
)
from analysis import page_data
from backtest import outcome_of, probabilities
from explore_predictors import SKIP_MATCHDAYS, compare
from predict import (
    MIN_MATCHES, SIM_RUNS, calibration, forecast, load, next_matchday, simulate_season,
    track_record, walk_forward,
)
from rating import EloRating
from score import clamp, normalize_to_power_score

# docs/ is what GitHub Pages serves from the main branch, so the generated page
# is the deploy - no build step, no workflow.
OUT_HTML = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")

SOURCE_URL = "https://www.fussball.de"


def snapshot(rows_all, matchday_n=None):
    """(played, scheduled, matchday) for rendering.

    For the current state (no matchday specified): use all played matches,
    load scheduled fixtures as-is.

    For a historical snapshot (matchday_n given): cut at the date of the
    first fixture with matchday > N. Played = matches before that date.
    Scheduled = remaining fixtures (with goals stripped, status="scheduled").
    Returns (played, scheduled, matchday_n).
    """
    if matchday_n is None:
        # Current state: all played matches, no future cutoff
        played = [r for r in rows_all if r["status"] == "played"]
        return played, None, max(int(r["matchday"]) for r in played) if played else 0

    # Historical snapshot: cut at the date of the first match of matchday N+1
    matchday_next = matchday_n + 1
    cut_dates = [r["date"] for r in rows_all if int(r["matchday"]) == matchday_next]
    if not cut_dates:
        # No such matchday exists; return all played matches, no future
        played = [r for r in rows_all if r["status"] == "played"]
        return played, [], matchday_n

    cut = min(cut_dates)
    played = [r for r in rows_all if r["status"] == "played" and r["date"] < cut]
    # Remaining matches (played after cut, or originally scheduled)
    remaining = [r for r in rows_all if r["date"] >= cut or r["status"] == "scheduled"]
    # Strip goals and mark as scheduled for snapshot
    scheduled = [{**r, "home_goals": "0", "away_goals": "0", "status": "scheduled"}
                 for r in remaining]

    return played, scheduled, matchday_n


def season_path(season):
    """Output path for a season page. Current season goes to docs/index.html,
    archived seasons go to docs/<season-slug>/index.html."""
    if season == SEASON_CURRENT:
        return OUT_HTML
    season_slug = season.replace("/", "-")
    return os.path.join(os.path.dirname(__file__), "..", "docs", season_slug, "index.html")


def season_href(from_season, to_season):
    """Relative link from one season page to another.

    From docs/index.html (current season):
      - to archive 2025-26: "2025-26/"
    From docs/2025-26/index.html (archive):
      - to current: "../"
      - to sibling 2024-25: "../2024-25/"
    """
    if from_season == to_season:
        return None  # same page, no link needed
    if from_season == SEASON_CURRENT:
        # Links from docs/index.html
        to_slug = to_season.replace("/", "-")
        return f"{to_slug}/"
    else:
        # Links from docs/<season>/index.html
        if to_season == SEASON_CURRENT:
            return "../"
        to_slug = to_season.replace("/", "-")
        return f"../{to_slug}/"

# The y-axis follows the data, but snapped to a 5-point grid and never narrower
# than Y_MIN_SPAN. Without that floor an early season - where the whole league
# sits inside three points - would be blown up to full height and fake movement
# that isn't there.
Y_GRID = 5
Y_MIN_SPAN = 15

# One colour per rank slot; only highlighted chart lines use theirs, the rest stay grey.
PALETTE = [
    "#1b6ca8", "#9c2f4a", "#1c7a58", "#d1802a", "#6d4fa2", "#0f8b9e", "#b8477e",
    "#5f7a1f", "#7a5445", "#3550a0", "#c2562a", "#2e8f3f", "#556070", "#86722a",
]


def num(x, decimals=1):
    """German decimal comma."""
    return f"{x:.{decimals}f}".replace(".", ",")


def load_logos(root=""):
    """Slug -> image URL. With root="" uses relative URLs to shared assets/logos/ folder.
    A team without a file renders without a crest rather than breaking the row."""
    if not os.path.isdir(LOGO_DIR):
        return {}
    logos = {}
    for name in sorted(os.listdir(LOGO_DIR)):
        if name.endswith(".png"):
            logos[name[:-4]] = f"{root}assets/logos/{name}"
    return logos


def font_face(family, filename, weights, root=""):
    """One @font-face with the woff2 reference (not embedded).
    Supports root paths for snapshot pages (e.g., root="../" or root="../../").
    Both files are the variable Latin cut Google Fonts serves, which
    covers German umlauts, the en dash and the minus sign the page uses."""
    path = os.path.join(FONT_DIR, filename)
    if not os.path.isfile(path):
        return ""
    return (
        "@font-face{font-family:'%s';font-style:normal;font-weight:%s;"
        "font-display:swap;src:url(%sassets/fonts/%s) format('woff2');}"
        % (family, weights, root, filename)
    )


def stroke(path):
    """One highlighter stroke as a background image: a wobbling outline filled
    with a gradient, so both the shape and the pressure are uneven. Stretched to
    whatever box it is given - a blob has no aspect ratio to preserve."""
    return (
        "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 200 40' preserveAspectRatio='none'%3E%3Cdefs%3E"
        "%3ClinearGradient id='g' x1='0' x2='1'%3E"
        "%3Cstop offset='0' stop-color='%23face3a' stop-opacity='.07'/%3E"
        "%3Cstop offset='.05' stop-color='%23face3a' stop-opacity='.44'/%3E"
        "%3Cstop offset='.34' stop-color='%23face3a' stop-opacity='.25'/%3E"
        "%3Cstop offset='.61' stop-color='%23face3a' stop-opacity='.42'/%3E"
        "%3Cstop offset='.87' stop-color='%23face3a' stop-opacity='.22'/%3E"
        "%3Cstop offset='1' stop-color='%23face3a' stop-opacity='0'/%3E"
        "%3C/linearGradient%3E%3C/defs%3E"
        f"%3Cpath d='{path}' fill='url(%23g)'/%3E%3C/svg%3E\")"
    )


STROKE_A = stroke("M1,9 C26,2 58,13 92,6 C128,0 163,10 199,3 "
                  "L198,31 C170,38 132,27 96,34 C62,40 27,30 2,37 Z")
STROKE_B = stroke("M2,13 C34,5 66,17 100,10 C134,3 168,13 198,5 "
                  "L197,29 C166,36 130,25 98,32 C64,38 30,28 3,36 Z")

# The pen stroke that ties a written note to the number it is about: a curve
# and a two-line head, drawn open so it reads as ink rather than as an icon.
ARROW = (
    '<svg class="arw" viewBox="0 0 48 22" aria-hidden="true">'
    '<path d="M1.5,16.5 C9,18.5 22,16.5 32,9"/>'
    '<path d="M25.5,5.5 L33.5,7.8 L29.5,14.5"/>'
    "</svg>"
)

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def short_date(iso):
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}.{d.month:02d}."


def signed(n):
    """Typographic minus, so the goal difference matches the form change column
    instead of setting a hyphen next to it."""
    return f"+{n}" if n >= 0 else f"&minus;{abs(n)}"


def pct(x):
    return f"{round(x * 100)}&nbsp;%"


def replay(rows):
    """Chronological Elo replay - the same order run.rank() uses, so the final
    ratings agree - recording each team's power score after each of its matches.

    A postponed match keeps its own matchday, so playing it later corrects that
    matchday's point in the chart while the ratings stay in true match order.
    """
    teams = sorted({r[side] for r in rows for side in ("home_team", "away_team")})
    elo = EloRating()
    elo.initialize_teams(teams)

    played = {t: 0 for t in teams}
    by_matchday = {t: {} for t in teams}  # team -> matchday -> power after that match

    for r in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        md = int(r["matchday"])
        elo.update_from_match(
            r["home_team"], r["away_team"], int(r["home_goals"]), int(r["away_goals"]), md
        )
        ratings = elo.get_ratings()
        for team in (r["home_team"], r["away_team"]):
            played[team] += 1
            by_matchday[team][md] = normalize_to_power_score(ratings[team], played[team])

    matchdays = sorted({int(r["matchday"]) for r in rows})
    series = {}
    for team in teams:
        values, last = [], None
        for md in matchdays:
            last = by_matchday[team].get(md, last)
            values.append(last)
        series[team] = values

    return elo.get_ratings(), played, matchdays, series


def to_power(rating):
    """Elo points to the 0-100 scale, without the small-sample shrinkage.

    The season column keeps the shrinkage (normalize_to_power_score); the form
    column cannot. With N0 = 20 a five-match window would keep a fifth of its
    deviation and the whole league would sit back on 50 - the column would show
    nothing. Same divisor, so the two numbers stay on one scale and a row can be
    read across.
    """
    return clamp(50 + (rating - R0) / POWER_SCALE_DIVISOR, 0, 100)


def form_series(rows, matchdays, window=FORM_WINDOW):
    """Elo over the last `window` matchdays only, recomputed for every matchday.

    This is the page's headline number, and it is a *description* of a stretch of
    football, not an estimate of strength. It answers the question the official
    table cannot: this team is twelfth, but how are they playing right now?

    A hard window, not a decay. Weighting old matches down instead - Elo with a
    higher K, or a drifting state-space model - was tried and cannot go this
    short: reweighting keeps every match in the estimate forever, so the
    effective memory stalls around nine matchdays and the values run off the
    0-100 scale before it gets shorter. Restarting from 1500 each matchday drops
    the old matches outright, which is the only way to get a five-match view.

    Elo rather than plain points because it matters *who* the five were against:
    two sides can both take twelve of fifteen and belong in different places.
    """
    teams = sorted({r[side] for r in rows for side in ("home_team", "away_team")})
    ordered = sorted(rows, key=lambda r: (r["date"], int(r["matchday"])))

    series = {team: [] for team in teams}
    for md in matchdays:
        elo = EloRating()
        elo.initialize_teams(teams)
        for r in ordered:
            if md - window < int(r["matchday"]) <= md:
                elo.update_from_match(
                    r["home_team"], r["away_team"],
                    int(r["home_goals"]), int(r["away_goals"]), int(r["matchday"]),
                )
        ratings = elo.get_ratings()
        for team in teams:
            series[team].append(to_power(ratings[team]))
    return series


def team_stats(rows):
    """Record and goals per team, straight from the results."""
    stats = defaultdict(lambda: {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})
    for r in rows:
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        h, a = stats[r["home_team"]], stats[r["away_team"]]
        h["gf"] += hg
        h["ga"] += ag
        a["gf"] += ag
        a["ga"] += hg
        if hg > ag:
            h["w"] += 1
            a["l"] += 1
            h["pts"] += 3
        elif hg < ag:
            h["l"] += 1
            a["w"] += 1
            a["pts"] += 3
        else:
            h["d"] += 1
            a["d"] += 1
            h["pts"] += 1
            a["pts"] += 1
    return stats


def last5_form(rows, n=5):
    """Each team's last n results, oldest to newest, as 'w'/'d'/'l' - the same
    chronological order the Elo replay uses, so the dots read left to right."""
    history = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        h, a = r["home_team"], r["away_team"]
        if hg > ag:
            history[h].append("w")
            history[a].append("l")
        elif hg < ag:
            history[h].append("l")
            history[a].append("w")
        else:
            history[h].append("d")
            history[a].append("d")
    return {team: results[-n:] for team, results in history.items()}


def official_positions(stats):
    """Position in the official table: points, then goal difference, then goals
    scored - the tie-breaks fussball.de uses before a direct comparison."""
    order = sorted(
        stats,
        key=lambda t: (stats[t]["pts"], stats[t]["gf"] - stats[t]["ga"], stats[t]["gf"]),
        reverse=True,
    )
    return {team: i + 1 for i, team in enumerate(order)}


def remaining(scheduled, powers):
    """Per team: fixtures still to play, oldest first, and the mean season power
    of the opponents.

    Returns {team: {"fixtures": [(date, matchday, opponent, is_home), ...],
                    "n_home": int, "n_away": int, "difficulty": float | None}}

    difficulty is the mean season power of remaining opponents, unadjusted for venue.
    It is None if the team has nothing left to play."""
    teams = {r[side] for r in scheduled for side in ("home_team", "away_team")}
    result = {team: {"fixtures": [], "n_home": 0, "n_away": 0, "difficulty": None}
              for team in teams}

    for r in sorted(scheduled, key=lambda r: (r["date"], int(r["matchday"]))):
        md = int(r["matchday"])
        hg, ag = r["home_team"], r["away_team"]
        is_h = {hg: True, ag: False}

        for team, opponent in ((hg, ag), (ag, hg)):
            result[team]["fixtures"].append((r["date"], md, opponent, is_h[team]))
            if is_h[team]:
                result[team]["n_home"] += 1
            else:
                result[team]["n_away"] += 1

    for team in result:
        if result[team]["fixtures"]:
            opp_powers = [powers.get(f[2], 50) for f in result[team]["fixtures"]]
            result[team]["difficulty"] = sum(opp_powers) / len(opp_powers)

    return result


def build_table(rows, scheduled=()):
    """Ranked by current form, with the season power score alongside it.

    Form leads because it is the one question the official table cannot answer -
    a reader already knows who has collected the points. Power stays in the row
    so the two readings sit side by side instead of competing for the headline.

    If scheduled rows are provided, includes difficulty (mean strength of remaining
    opponents) and remaining fixture data for each team.
    """
    ratings, played, matchdays, series = replay(rows)
    forms = form_series(rows, matchdays)
    stats = team_stats(rows)
    positions = official_positions(stats)
    dots = last5_form(rows)

    # Prepare power scores for remaining calculation
    powers = {team: normalize_to_power_score(ratings[team], played[team])
              for team in ratings}
    remaining_data = remaining(scheduled, powers) if scheduled else {}

    table = []
    for team, values in series.items():
        f = forms[team]
        s = stats[team]
        row = {
            "team": team,
            "form": f[-1],
            "delta": None if len(f) < 2 else f[-1] - f[-2],
            "power": values[-1],
            "matches": played[team],
            "record": (s["w"], s["d"], s["l"]),
            "dots": dots[team],
            "gf": s["gf"],
            "ga": s["ga"],
            "position": positions[team],
            "series": values,
            "fseries": f,
        }
        if scheduled and team in remaining_data:
            row["remaining"] = remaining_data[team]
        table.append(row)
    table.sort(key=lambda t: t["form"], reverse=True)
    return table, matchdays


def y_axis(table, key="series"):
    """Snapped, minimum-width range covering every plotted value."""
    values = [v for t in table for v in t[key]]
    lo = int(math.floor((min(values) - 1) / Y_GRID)) * Y_GRID
    hi = int(math.ceil((max(values) + 1) / Y_GRID)) * Y_GRID
    while hi - lo < Y_MIN_SPAN:
        lo -= Y_GRID
        if hi - lo < Y_MIN_SPAN:
            hi += Y_GRID
    step = Y_GRID if hi - lo <= 30 else 2 * Y_GRID
    return lo, hi, step


# The jersey outline, drawn once around its own centre so placing a token is a
# translate: shoulders at -18, hem at 19, sleeves out to +-21. The crest sits on
# a white patch on the chest, the rank badge on the lower right of the hem.
JERSEY = (
    "M-7,-18 L-13,-18 L-21,-10 L-15.5,-1 L-12,-5.5 L-12.5,19 "
    "L12.5,19 L12,-5.5 L15.5,-1 L21,-10 L13,-18 L7,-18 "
    "C6.5,-13 -6.5,-13 -7,-18 Z"
)
# The jersey is drawn at TOKEN_SCALE, so sleeve to sleeve is 42 * scale; the gap
# leaves a little air before a token is pushed a lane up.
TOKEN_SCALE = 1.25
TOKEN_GAP = 62
# Vertical offset per beeswarm lane, middle outwards.
LANE_OFFSETS = (0, -52, 52, -104, 104)


def hero_half_span(table):
    """Half-width of the pitch scale, snapped to the same 5-point grid, symmetric
    around the 50-point league average."""
    half = max(abs(t["form"] - 50) for t in table)
    return max(int(math.ceil(half / Y_GRID)) * Y_GRID, Y_GRID)


def lanes(xs, min_gap, n=5):
    """Beeswarm: push tokens that would overlap into a neighbouring lane."""
    last = [-1e9] * n
    out = [0] * len(xs)
    for i in sorted(range(len(xs)), key=lambda i: xs[i]):
        slot = next((k for k in range(n) if xs[i] - last[k] >= min_gap), None)
        if slot is None:
            slot = min(range(n), key=lambda k: last[k])
        out[i] = slot
        last[slot] = xs[i]
    return out


def svg_pitch(table, logos):
    """The league as a formation on a landscape pitch: one token per team along
    the long axis, right of the 50-point league average green, left wine.

    Every token is a jersey carrying the club crest, with the rank in a corner
    badge so it can be traced straight into the table beside it; a team without
    a crest wears the rank as its shirt number instead. All labelling is HTML
    around the drawing - SVG text scaled down to a phone would shrink to a few
    pixels.
    """
    w, pad = 1600, 12
    half = hero_half_span(table)
    lo, hi = 50 - half, 50 + half
    inner_l, inner_r = pad + 120, w - pad - 120

    def x(p):
        return inner_l + (inner_r - inner_l) * (p - lo) / (hi - lo)

    at = [x(t["form"]) for t in table]
    lane = lanes(at, TOKEN_GAP)
    # The pitch is only as deep as the beeswarm actually got. Drawn at a fixed
    # depth it left a third of the grass empty above and below the tokens, which
    # reads as missing teams rather than as a tightly packed league.
    used = max(abs(LANE_OFFSETS[k]) for k in lane)
    h = 2 * (used + 78)
    cy = h // 2
    box_h = min(192, h - 2 * pad - 16)
    goal_h = box_h * 0.46

    cx = x(50)
    p = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Alle {len(table)} Teams nach Form '
        f'von {lo} links bis {hi} rechts; die Nummer am Trikot ist der Rang">'
    ]
    # Pitch markings - the frame the scale is read against, nothing else.
    p.append(
        f'<g class="pg">'
        f'<rect x="{pad}" y="{pad}" width="{w - 2 * pad}" height="{h - 2 * pad}" rx="3"/>'
        f'<rect x="{pad}" y="{cy - box_h / 2:.1f}" width="104" height="{box_h}"/>'
        f'<rect x="{w - pad - 104}" y="{cy - box_h / 2:.1f}" width="104" height="{box_h}"/>'
        f'<rect x="{pad}" y="{cy - goal_h / 2:.1f}" width="36" height="{goal_h:.1f}"/>'
        f'<rect x="{w - pad - 36}" y="{cy - goal_h / 2:.1f}" width="36" height="{goal_h:.1f}"/>'
        f'<circle cx="{cx:.1f}" cy="{cy}" r="{min(58, box_h / 2.6):.1f}"/>'
        f"</g>"
    )
    # The halfway line is the league average, and it is the one reference the
    # whole drawing is read against - drawn solid, not implied by the circle.
    p.append(
        f'<line class="half" x1="{cx:.1f}" y1="{pad}" x2="{cx:.1f}" y2="{h - pad}"/>'
        f'<line class="axis" x1="{inner_l - 40}" y1="{cy}" x2="{inner_r + 40}" y2="{cy}"/>'
    )
    # A chalk mark every five points, so a jersey's distance from the middle can
    # be counted off instead of only compared. Labelled in HTML at the ends -
    # SVG text scaled to a phone would be a few pixels tall.
    for v in range(lo, hi + 1, Y_GRID):
        if v != 50:
            p.append(f'<line class="stick" x1="{x(v):.1f}" y1="{cy - 7}" '
                     f'x2="{x(v):.1f}" y2="{cy + 7}"/>')

    for i, t in enumerate(table):
        dy = LANE_OFFSETS[lane[i]]
        cls = "pos" if t["form"] >= 50 else "neg"
        crest = logos.get(team_slug(t["team"]))
        if crest:
            face = (
                f'<circle class="in" cy="6.5" r="11.5"/>'
                f'<image href="{crest}" x="-10" y="-3.5" width="20" height="20" '
                f'preserveAspectRatio="xMidYMid meet"/>'
                f'<circle class="rkb" cx="16" cy="16" r="8"/>'
                f'<text class="rk" x="16" y="20">{i + 1}</text>'
            )
        else:
            face = f'<text y="14">{i + 1}</text>'
        p.append(
            f'<g class="ptok" '
            f'transform="translate({at[i]:.1f} {cy + dy}) scale({TOKEN_SCALE})">'
            f'<path class="{cls}" d="{JERSEY}"/>'
            f"{face}"
            f"<title>{i + 1}. {html.escape(t['team'])} - {num(t['form'])}</title></g>"
        )
    p.append("</svg>")
    return "\n".join(p), lo, hi


def svg_chart(table, matchdays, key="series", prefix="line", highlight=()):
    """Inline SVG, one polyline per team, each ending in its rank token so a
    highlighted line can be named without looking anywhere else. `highlight`
    names the ranks drawn in colour and on top; the rest stay grey."""
    w, h = 900, 400
    left, right, top, bottom = 44, 34, 18, 36
    tick_x = left - 8
    span = max(len(matchdays) - 1, 1)
    y_min, y_max, y_step = y_axis(table, key)

    def x(i):
        return left + (w - left - right) * i / span

    def y(power):
        frac = (power - y_min) / (y_max - y_min)
        return h - bottom - (h - bottom - top) * frac

    label = "Formverlauf" if key == "fseries" else "Verlauf der Saisonwerte"
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label} je Spieltag">']

    # Same reading direction as everywhere else: the half above 50 is green.
    if y_min <= 50 <= y_max:
        parts.append(
            f'<rect class="zone pos" x="{left}" y="{top}" width="{w - left - right}" '
            f'height="{y(50) - top:.1f}"/>'
            f'<rect class="zone neg" x="{left}" y="{y(50):.1f}" width="{w - left - right}" '
            f'height="{h - bottom - y(50):.1f}"/>'
        )

    for tick in range(y_min, y_max + 1, y_step):
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y(tick):.1f}" x2="{w - right}" '
            f'y2="{y(tick):.1f}"/>'
            f'<text class="tick" x="{tick_x}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{tick}</text>'
        )
    for i, md in enumerate(matchdays):
        if len(matchdays) <= 14 or md % 2 == 0 or i == len(matchdays) - 1:
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{h - 13}" '
                f'text-anchor="middle">{md}</text>'
            )
    highlight = set(highlight)
    for rank in sorted(range(len(table)), key=lambda r: r in highlight):
        t = table[rank]
        values = t[key]
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.2"/>' for i, v in enumerate(values)
        )
        ex, ey = x(len(values) - 1), y(values[-1])
        cls = "pos" if values[-1] >= 50 else "neg"
        end = (
            f'<g class="end"><circle class="{cls}" cx="{ex:.1f}" cy="{ey:.1f}" r="13"/>'
            f'<text x="{ex:.1f}" y="{ey + 4.6:.1f}">{rank + 1}</text></g>'
        )
        parts.append(
            f'<g class="line{" sel" if rank in highlight else ""}" id="{prefix}{rank}" '
            f'style="--c:{PALETTE[rank % len(PALETTE)]}">'
            f'<polyline points="{points}"/>{dots}{end}</g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def snapshot_banner(season, matchday_n):
    """Info banner for historical snapshot pages. Path to the current page is
    always two levels up: docs/<season>/spieltag-NN/index.html -> docs/index.html."""
    if season != SEASON_CURRENT or matchday_n is None:
        return ""
    return f"""<div class="banner snapshot">
  <p>Stand nach Spieltag {matchday_n} – so sah die Seite damals aus.
  <a href="../../" class="current-link">→ Aktueller Stand</a></p>
</div>"""


def matchday_nav(season, current_matchday, season_max_matchday, on_snapshot):
    """HTML for matchday selector in the masthead. Links to snapshot pages.

    Every matchday from 1..season_max_matchday has a snapshot page (write_season()
    writes them all together), so every number except the current one is a link -
    there is no "future" state to grey out within a season already played that far.

    on_snapshot: True if this page itself is docs/<season>/spieltag-NN/index.html
    (links to siblings are "../spieltag-MM/"), False if it is docs/index.html
    (links are "<season-slug>/spieltag-MM/").
    """
    if season != SEASON_CURRENT or current_matchday is None:
        return ""

    season_slug = season.replace("/", "-")
    prefix = "../" if on_snapshot else f"{season_slug}/"

    links = []
    for md in range(1, season_max_matchday + 1):
        if md == current_matchday:
            links.append(f'<span aria-current="page">{md}</span>')
        else:
            links.append(f'<a href="{prefix}spieltag-{md:02d}/">{md}</a>')

    nav_prev = ""
    nav_next = ""
    if current_matchday > 1:
        nav_prev = f'<a href="{prefix}spieltag-{current_matchday-1:02d}/" class="nav-prev">‹</a>'
    if current_matchday < season_max_matchday:
        nav_next = f'<a href="{prefix}spieltag-{current_matchday+1:02d}/" class="nav-next">›</a>'

    return f'<nav class="matchdays">{nav_prev}{"".join(links)}{nav_next}</nav>'


def season_switcher(current_season):
    """HTML for the season switcher in the masthead. Lists all seasons in STAFFEL_IDS,
    newest first, with relative links between archive pages."""
    seasons = sorted(STAFFEL_IDS.keys(), reverse=True)
    links = []
    for season in seasons:
        href = season_href(current_season, season)
        if href is None:
            # Current page
            links.append(f'<span>{season}</span>')
        else:
            links.append(f'<a href="{href}">{season}</a>')
    return f'<nav class="seasons">{"".join(links)}</nav>'


def rest_cell(remaining_data):
    """HTML for the remaining schedule difficulty cell.

    Shows mean opponent strength and home/away split.
    Returns empty string if no remaining data or no fixtures left."""
    if not remaining_data or remaining_data.get("difficulty") is None:
        return '<td class="s-hide season">&ndash;</td>'

    difficulty = remaining_data["difficulty"]
    n_h = remaining_data["n_home"]
    n_a = remaining_data["n_away"]
    return (f'<td class="s-hide season"><div class="pw"><b>{num(difficulty)}</b></div>'
            f'<span class="rs">{n_h} H / {n_a} A</span></td>')


def crest_img(logos, team):
    crest = logos.get(team_slug(team))
    return f'<img class="lg sm" src="{crest}" alt="">' if crest else ""


def match_probabilities(rows, season):
    """Per-match probabilities (p_away, p_draw, p_home) keyed by row index.
    Only for current season and only if enough matches have been played.
    Returns {match_idx: (p_away, p_draw, p_home), ...} or {} if not applicable."""
    if season != SEASON_CURRENT or len(rows) < MIN_MATCHES:
        return {}

    params = calibration()
    result = {}
    for match_idx, (r, diff) in enumerate(walk_forward(rows)):
        result[match_idx] = probabilities(diff, params)
    return result


# A win the forecast gave less than one in four. The same floor marks the
# "Überraschung" badge in the team panels, so the card and the badge agree.
SURPRISE_MAX_P = 0.25


def surprise_card(rows, table, matchday, logos):
    """The win of the latest matchday that the forecast gave the smallest chance,
    with the numbers a reader needs to check it: the pre-match percentages and
    both sides' form and table place before and after.

    Wins only. A draw is the least likely outcome of every pairing here - it
    comes out at 13-19% whichever two sides meet - so by probability alone almost
    every matchday's surprise would be a 1:1: dropped points, but not a story.
    """
    probs = {}
    for r, diff in walk_forward(rows):
        if int(r["matchday"]) == matchday:
            probs[r["match_id"]] = probabilities(diff, calibration())
    if not probs:
        return ('<div class="card surprise"><p class="none">Noch keine Prognose – dafür braucht '
                'es zwei gespielte Spieltage.</p></div>')

    best = None
    for r in rows:
        if int(r["matchday"]) != matchday or r["match_id"] not in probs:
            continue
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        if hg == ag:
            continue
        p_win = probs[r["match_id"]][2 if hg > ag else 0]
        if p_win < SURPRISE_MAX_P and (best is None or p_win < best[1]):
            best = (r, p_win)
    if best is None:
        return ('<div class="card surprise"><p class="none">Diesen Spieltag keine – jeder Sieg '
                'hatte vorher mindestens eine Chance von eins zu vier.</p></div>')

    r, p_win = best
    p_away, p_draw, p_home = probs[r["match_id"]]
    home_won = int(r["home_goals"]) > int(r["away_goals"])
    winner = r["home_team"] if home_won else r["away_team"]

    # Form and table place as they stood before this matchday, against where
    # they are now - the first says why it was a surprise, the second what it
    # did. Form as the number, not the rank: a bottom side stays fourteenth
    # after its upset, but its form moves, and that is the point.
    before, _ = build_table([x for x in rows if int(x["matchday"]) < matchday])
    ranks = {}
    for tab, key in ((before, "pre"), (table, "post")):
        for t in tab:
            ranks.setdefault(t["team"], {})[key] = (t["form"], t["position"])

    sides = "".join(
        f'<div class="fxt{" win" if r[side] == winner else ""}">{crest_img(logos, r[side])}'
        f'<span>{html.escape(r[side])}</span></div>' for side in ("home_team", "away_team")
    )
    bar = "".join(f'<i class="{cls}" style="width:{p:.1%}"></i>'
                  for cls, p in (("w", p_home), ("d", p_draw), ("l", p_away)))
    pcts = " · ".join(
        f'<b>{label} {pct(p)}</b>' if won else f'{label} {pct(p)}'
        for label, p, won in (("Heim", p_home, home_won), ("Remis", p_draw, False),
                              ("Auswärts", p_away, not home_won))
    )
    facts = []
    for side in ("home_team", "away_team"):
        team = r[side]
        (f0, t0), (f1, t1) = ranks[team]["pre"], ranks[team]["post"]
        facts.append(
            f'<tr><td class="l">{html.escape(team)}</td>'
            f'<td>{num(f0)} <span class="to">→ {num(f1)}</span></td>'
            f'<td>{t0}. <span class="to">→ {t1}.</span></td></tr>'
        )
    n = sum(1 for x in rows if int(x["matchday"]) == matchday)
    return f"""<div class="card surprise">
          <div class="smatch">{sides}<b class="score">{r["home_goals"]}:{r["away_goals"]}</b></div>
          <div class="bar">{bar}</div>
          <p class="sprob">Vorher: {pcts}</p>
          <table class="sfacts">
            <thead><tr><th class="l">Vorher → jetzt</th><th>Form</th><th>Tabelle</th></tr></thead>
            <tbody>{"".join(facts)}</tbody>
          </table>
          <p class="hint">{html.escape(winner)} gewinnt mit einer Chance von {pct(p_win)} vorher –
          von den {n} Spielen des Spieltags der Sieg, den das Modell am wenigsten erwartet hat.</p>
        </div>"""


def surface_section(surface):
    """The pitch table: goals per match and the home bonus per surface, over
    every season on file. Empty string without detail data (a fresh clone)."""
    if not surface.get("alle"):
        return ""
    order = ("Rasen", "Kunstrasen", "Hartplatz")
    top = max(s["home_bonus"] for s in surface.values())
    body = []
    for key in [k for k in order if k in surface] + ["alle"]:
        s = surface[key]
        label = "Liga gesamt" if key == "alle" else key
        width = max(s["home_bonus"], 0) / top if top > 0 else 0
        body.append(
            f'<tr class="{"total" if key == "alle" else ""}">'
            f'<td class="l">{label}<span class="n">{s["n"]} Spiele</span></td>'
            f'<td>{num(s["goals"], 2)}</td>'
            f'<td class="hb"><b>{signed(round(s["home_bonus"]))}</b>'
            f'<span class="hbar"><i style="width:{width:.0%}"></i></span></td></tr>'
        )
    grass = round(surface["Rasen"]["home_bonus"]) if "Rasen" in surface else round(surface["alle"]["home_bonus"])
    return f"""<h3>Belag</h3>
        <div class="card">
          <table class="sfacts belag">
            <thead><tr><th class="l">Platz</th><th>Tore/Spiel</th><th>Heimbonus</th></tr></thead>
            <tbody>{"".join(body)}</tbody>
          </table>
        </div>
        <p class="hint"><strong>Heimbonus</strong>: um wie viele Prozentpunkte der Gastgeber besser
        abschneidet, als die Stärke beider Teams erwarten lässt – +{grass} auf Rasen heißt
        {grass} Siege mehr aus hundert Heimspielen. Ein Hinweis, kein Beweis.</p>"""


def comeback_section(marks, first, logos):
    """Two marked teams by a fixed rule with a floor, and the league's own
    first-goal rates underneath so a reader can tell how rare a comeback is."""
    if not first["n"]:
        return ""
    lines = []
    for key, label, pts, n, noun in (
        ("comeback", "Comeback-Team", "pts_after_trailing", "trailed", "Spielen mit Rückstand"),
        ("dropped", "Führung verspielt", "pts_dropped_leading", "led", "Führungen liegen gelassen"),
    ):
        if marks[key] is None:
            lines.append(f'<div class="cb"><span class="badge">{label}</span>'
                         f'<p class="none">diesen Spieltag keines</p></div>')
            continue
        team, t = marks[key]
        extra = ""
        if key == "dropped" and t["lost_after_leading"]:
            extra = f', {t["lost_after_leading"]} davon noch verloren'
        if key == "comeback" and t["won_after_trailing"]:
            extra = f', {t["won_after_trailing"]} davon noch gewonnen'
        lines.append(
            f'<div class="cb"><span class="badge">{label}</span>'
            f'<div class="fxt">{crest_img(logos, team)}<span>{html.escape(team)}</span></div>'
            f'<p><b>{t[pts]} Punkte</b> aus {t[n]} {noun}{extra}.</p></div>'
        )
    n = first["n"]
    bar = "".join(f'<i class="{cls}" style="width:{first[k] / n:.1%}"></i>'
                  for cls, k in (("w", "win"), ("d", "draw"), ("l", "loss")))
    return f"""<h3>Rückstand und Führung</h3>
        <div class="card cbs">
          {"".join(lines)}
          <div class="first"><p>Wer das erste Tor schießt, gewinnt <b>{pct(first["win"] / n)}</b> der
          Spiele, {pct(first["draw"] / n)} enden remis, {pct(first["loss"] / n)} gehen noch
          verloren.</p><div class="bar">{bar}</div></div>
        </div>
        <p class="hint">Aus den Torminuten dieser Saison: die meisten Punkte nach Rückstand und
        die meisten aus eigener Führung liegen gelassen.</p>"""


def team_stats_by_venue(rows, team):
    """Record (W-D-L) for a team, split by home and away."""
    home = {"w": 0, "d": 0, "l": 0}
    away = {"w": 0, "d": 0, "l": 0}
    for r in rows:
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        if r["home_team"] == team:
            v = home
            is_home = True
        elif r["away_team"] == team:
            v = away
            is_home = False
        else:
            continue
        if hg > ag:
            v["w" if is_home else "l"] += 1
        elif hg < ag:
            v["l" if is_home else "w"] += 1
        else:
            v["d"] += 1
    return home, away


def team_details_html(table, rows, logos, season, matchdays, match_probs):
    """One section with a team dropdown and one panel per team, all hidden except
    rank 0; the dropdown switches panels.

    Each panel shows: head (crest, form rank, form, power, position, W-D-L split),
    season chart (highlighted), results list with probabilities, remaining fixtures."""

    options = "".join(
        f'<option value="{rank}">{rank + 1}. {html.escape(t["team"])}</option>'
        for rank, t in enumerate(table)
    )
    sections = []
    for rank, team_row in enumerate(table):
        team = team_row["team"]
        w, d, l = team_row["record"]
        w_h, d_h, l_h = team_row["record"]  # Will be replaced with venue splits
        w_a, d_a, l_a = team_row["record"]

        # Venue split
        home_stats, away_stats = team_stats_by_venue(rows, team)
        w_h, d_h, l_h = home_stats["w"], home_stats["d"], home_stats["l"]
        w_a, d_a, l_a = away_stats["w"], away_stats["d"], away_stats["l"]

        # Team head info
        crest = logos.get(team_slug(team))
        crest_html = f'<img class="lg" src="{crest}" alt="">' if crest else ""
        position_gap = team_row["position"] - (rank + 1)
        if position_gap > 0:
            gap_cls = "up"
            gap_txt = f"+{position_gap}"
        elif position_gap < 0:
            gap_cls = "down"
            gap_txt = f"−{abs(position_gap)}"
        else:
            gap_cls = "flat"
            gap_txt = "±0"

        head = f"""    <div class="thead">
      <div class="tchest">{crest_html}
        <div>
          <span class="tname">{html.escape(team)}</span>
          <div class="tstats">
            <span class="form"><b>{num(team_row["form"])}</b> <span class="trank">Rang {rank + 1}</span></span>
            <span class="tpower">Saison <b>{num(team_row["power"])}</b></span>
            <span class="tpos">Tabelle <b>{team_row["position"]}</b> <span class="{gap_cls}">({gap_txt})</span></span>
            <span class="trecord"><b>{w_h}-{d_h}-{l_h}</b> zu Hause, <b>{w_a}-{d_a}-{l_a}</b> auswärts</span>
          </div>
        </div>
      </div>
    </div>"""

        # Results list (current season only)
        results_html = ""
        if season == SEASON_CURRENT and match_probs:
            results = []
            for r in sorted(rows, key=lambda x: (x["date"], int(x["matchday"]))):
                if r["home_team"] != team and r["away_team"] != team:
                    continue
                is_home = r["home_team"] == team
                opponent = r["away_team"] if is_home else r["home_team"]
                hg, ag = int(r["home_goals"]), int(r["away_goals"])
                score = f"{hg}:{ag}"
                outcome = outcome_of(hg, ag)

                # The column always shows the same thing - this team's chance of
                # winning, as the model saw it before kick-off - so a reader can
                # compare rows. The surprise note marks a win the model gave
                # less than a one-in-four chance - the rule of the matchday card.
                prob_txt = "–"
                surprise_badge = ""
                for idx, (wf_r, _) in enumerate(walk_forward(rows)):
                    if (wf_r["date"] == r["date"] and
                        wf_r["home_team"] == r["home_team"] and
                        wf_r["away_team"] == r["away_team"]):
                        if idx in match_probs:
                            p = match_probs[idx]
                            prob_txt = pct(p[2] if is_home else p[0])
                            if hg != ag and p[outcome] < SURPRISE_MAX_P:
                                surprise_badge = '<span class="badge">Überraschung</span>'
                        break

                # Read from this team's side: 2:1 away is a loss, and the reader
                # should not have to work that out from the H/A marker.
                team_goals, opp_goals = (hg, ag) if is_home else (ag, hg)
                res = "w" if team_goals > opp_goals else ("d" if team_goals == opp_goals else "l")
                res_letter = {"w": "S", "d": "U", "l": "N"}[res]

                opp_side = "H" if is_home else "A"
                results.append(
                    f'<tr><td class="md">{r["matchday"]}</td>'
                    f'<td class="dt">{short_date(r["date"])}</td>'
                    f'<td class="opp">{html.escape(opponent)} <span class="vs">{opp_side}</span></td>'
                    f'<td class="sc"><span class="res {res}">{res_letter}</span>{score}</td>'
                    f'<td class="pr">{prob_txt}{surprise_badge}</td></tr>'
                )

            results_html = f"""    <h3>Bisherige Ergebnisse</h3>
    <div class="card">
      <table class="tres">
        <thead>
          <tr><th>MD</th><th>Datum</th><th>Gegner</th><th>Ergebnis</th><th>Siegchance vorher</th></tr>
        </thead>
        <tbody>
          {"".join(results)}
        </tbody>
      </table>
    </div>
    <p class="hint"><span class="res w">S</span>Sieg, <span class="res d">U</span>Unentschieden,
    <span class="res l">N</span>Niederlage aus Sicht dieses Teams; H und A: zu Hause oder
    auswärts. <strong>Siegchance vorher</strong> ist, wie wahrscheinlich das Modell vor dem
    Anpfiff einen Sieg dieses Teams fand.</p>
"""

        # Remaining fixtures
        remaining_html = ""
        if team_row.get("remaining"):
            remaining = team_row["remaining"]["fixtures"]
            if remaining:
                fixtures = []
                for date_iso, md, opp, is_home in remaining:
                    opp_side = "H" if is_home else "A"
                    opp_power = None
                    for t in table:
                        if t["team"] == opp:
                            opp_power = t["power"]
                            break
                    power_txt = num(opp_power) if opp_power else "–"
                    fixtures.append(
                        f'<tr><td class="md">{md}</td>'
                        f'<td class="dt">{short_date(date_iso)}</td>'
                        f'<td class="opp">{html.escape(opp)} <span class="vs">{opp_side}</span></td>'
                        f'<td class="pw">{power_txt}</td></tr>'
                    )

                remaining_html = f"""    <h3>Verbleibende Gegner</h3>
    <div class="card">
      <table class="tres">
        <thead>
          <tr><th>MD</th><th>Datum</th><th>Gegner</th><th>Stärke</th></tr>
        </thead>
        <tbody>
          {"".join(fixtures)}
        </tbody>
      </table>
    </div>
"""

        # Same two-column grid as the dashboard: facts and tables on the left,
        # the chart on the right and sticky, so the two views read as one sheet.
        section = f"""    <div class="tpanel dash" data-rank="{rank}" hidden>
      <div class="col">
{head}
{results_html}{remaining_html}
      </div>
      <div class="col chartcol">
        <h3>Saisonverlauf</h3>
        <div class="card chart">{svg_chart(table, matchdays, "series", "season")}</div>
        <p class="hint">X-Achse: Spieltag, Y-Achse: Saisonwert über alle bisherigen Spiele.
        Die anderen Linien bleiben grau zum Vergleich.</p>
      </div>
    </div>
"""
        sections.append(section)

    return f"""  <section class="tdet">
    <h2>Team im Detail</h2>
    <p class="sub">Saisonverlauf, alle Ergebnisse und die verbleibenden Gegner eines Teams.</p>
    <label class="tpick">Team <select id="tpick">{options}</select></label>
{"".join(sections)}  </section>
"""


def forecast_section(season, played, logos, forms, scheduled=None):
    """The next matchday as probabilities. Empty string if nothing is scheduled,
    which is what a finished season looks like."""
    if scheduled is None:
        scheduled = load(season, "scheduled")
    matchday, fixtures = next_matchday(scheduled)
    tips = forecast(played, fixtures)
    if not tips:
        return ""

    # The one fixture worth marking, by the page's own headline number: the pair
    # with the best combined form. Deliberately not "closest percentages" - those
    # sit inside a few points of each other all season, so picking the tightest
    # one would be marking noise.
    top = max(range(len(tips)),
              key=lambda i: forms.get(tips[i]["home"], 50) + forms.get(tips[i]["away"], 50))

    body = []
    for i, t in enumerate(tips):
        best = max(("p_home", "p_draw", "p_away"), key=t.__getitem__)
        cells = "".join(
            f'<td class="p{" best" if key == best else ""}">{pct(t[key])}</td>'
            for key in ("p_home", "p_draw", "p_away")
        )
        sides = "".join(
            f'<div class="fxt">{crest_img(logos, t[side])}'
            f'<span>{html.escape(t[side])}</span></div>' for side in ("home", "away")
        )
        bar = "".join(f'<i class="{cls}" style="width:{t[key]:.1%}"></i>'
                      for cls, key in (("w", "p_home"), ("d", "p_draw"), ("l", "p_away")))
        mark = '<span class="badge">Topspiel</span>' if i == top else ""
        body.append(
            f'<tr class="{"hl marked" if i == top else ""}">'
            f'<td class="l dt">{short_date(t["date"])}</td>'
            f'<td class="l fx">{sides}<div class="bar">{bar}</div>{mark}</td>'
            f'{cells}'
            f'<td class="xg s-hide">{num(t["xg_home"] + t["xg_away"])}</td></tr>'
        )

    record = track_record(played)
    if record:
        ahead = "vorn" if record["rps"] < record["base_rps"] else "hinten"
        balance = (
            f'<strong>Bilanz:</strong> {record["hits"]} von {record["n"]} Spielen dieser '
            f'Saison richtig; als Fehlerwert {num(record["rps"], 3)} gegen '
            f'{num(record["base_rps"], 3)} für die stur getippte Liga-Quote, die Prognose '
            f'liegt also knapp {ahead}.'
        )
    else:
        balance = "<strong>Bilanz:</strong> noch zu wenige Spiele."

    return f"""<section class="fcast">
    <h2>Prognose für Spieltag {matchday}</h2>
    <p class="sub">Was das Modell für die nächsten Spiele erwartet – Wahrscheinlichkeiten,
    keine Tipps.</p>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th class="l">Termin</th><th class="l">Begegnung</th>
            <th>Heim</th><th>Remis</th><th>Ausw.</th>
            <th class="s-hide">Tore erwartet</th>
          </tr>
        </thead>
        <tbody>
          {chr(10).join("          " + r for r in body).strip()}
        </tbody>
      </table>
    </div>
    <p class="hint">Aus den bisherigen Ergebnissen bekommt jedes Team eine Angriffs- und eine
    Abwehrstärke; daraus folgen die erwarteten Tore und die drei Prozentwerte. Dass ein Remis nie
    vorn liegt und die Prozente eng beieinander liegen, ist so gemessen – warum, steht in den
    <a href="#methodik">Erklärungen</a>.</p>
    <p class="hint">{balance}</p>
  </section>
"""


def simulation_section(season, rows, logos, scheduled):
    """Season outcome probabilities from Monte Carlo simulation.
    Empty string if nothing is scheduled or not enough matches played."""
    if season != SEASON_CURRENT or not scheduled:
        return ""

    sim = simulate_season(rows, scheduled)
    if not sim:
        return ""

    # Sort by expected final points, descending
    sorted_teams = sorted(sim.items(),
                         key=lambda x: x[1]["exp_pts"],
                         reverse=True)

    body = []
    for team, data in sorted_teams:
        pts = int(round(data["pts"]))
        exp_pts = int(round(data["exp_pts"]))
        p_first = data["p_first"]
        p_bottom2 = data["p_bottom2"]

        # Bars
        first_bar = f'<i class="up" style="width:{p_first:.1%}"></i>' if p_first > 0.005 else ""
        bottom_bar = f'<i class="down" style="width:{p_bottom2:.1%}"></i>' if p_bottom2 > 0.005 else ""

        first_pct = f"{round(p_first * 100)}%" if p_first >= 0.005 else "&lt;1%"
        bottom_pct = f"{round(p_bottom2 * 100)}%" if p_bottom2 >= 0.005 else "&lt;1%"

        crest = logos.get(team_slug(team))
        crest_html = f'<img class="lg sm" src="{crest}" alt="">' if crest else ""

        body.append(
            f'<tr><td class="l">{crest_html}<span>{html.escape(team)}</span></td>'
            f'<td>{pts}</td><td>{exp_pts}</td>'
            f'<td><div class="bar">{first_bar}</div>{first_pct}</td>'
            f'<td><div class="bar">{bottom_bar}</div>{bottom_pct}</td></tr>'
        )

    return f"""<section class="sim">
    <h2>Saisonausblick</h2>
    <p class="sub">Wahrscheinlichkeit, die Meisterschaft zu gewinnen oder abzusteigen.</p>
    <div class="card">
      <table>
        <thead>
          <tr><th class="l">Team</th><th>Punkte</th><th>Erwartet</th><th>Meister %</th><th>Abstieg %</th></tr>
        </thead>
        <tbody>
          {chr(10).join("          " + r for r in body).strip()}
        </tbody>
      </table>
    </div>
    <p class="hint">Die verbleibenden {len(scheduled)} Spiele werden {SIM_RUNS}-mal mit den
    erwarteten Toren der Prognose durchgespielt und die Tabellen gezählt. Unter 1&nbsp;% heißt
    nicht null.</p>
  </section>
"""


def predictor_section():
    """The measured table of things that ought to predict better. None does, and
    showing that is the honest way to publish a forecast at all."""
    body = []
    for r in compare():
        lead = ("<span class=\"flat\">Referenz</span>" if r["baseline"] else
                f'{num(r["lead"] * 1000, 1)} <span class="pm">± {num(r["se"] * 1000, 1)}</span>')
        # The zero point everything else is measured against gets ruled off, the
        # way a sheet rules off the line a column is totalled on.
        body.append(f'<tr class="{"base" if r["baseline"] else ""}">'
                    f'<td class="l">{r["name"]}</td>'
                    f'<td>{num(r["rps"], 4)}</td><td class="ld">{lead}</td></tr>')

    return f"""<section class="fcast">
    <h2>Was besser sein müsste – und es nicht ist</h2>
    <p class="sub">Jeder dieser Ansätze wurde mit demselben Verfahren in Wahrscheinlichkeiten
    umgerechnet und an der Saison {SEASON_PREVIOUS} nachgerechnet.</p>
    <div class="card">
      <table>
        <thead>
          <tr><th class="l">Ansatz</th><th>Fehler</th><th>Vorsprung auf die Liga-Quote</th></tr>
        </thead>
        <tbody>
          {chr(10).join("          " + r for r in body).strip()}
        </tbody>
      </table>
    </div>
    <p class="hint">Kleinerer Fehler ist besser; <strong>Vorsprung</strong> ist der Abstand zur
    simplen Auskunft „meistens gewinnt das Heimteam“. Kein Ansatz ist nachweisbar besser als
    diese Auskunft – deshalb ist die Prognose ein Blickwinkel, kein Tipp.</p>
  </section>
"""


def method_section(has_future):
    """The long-form explanations behind every number on the page, on a tab of
    their own so the dashboard can keep its notes to a sentence or two. Static
    prose except for the few constants it quotes."""
    rest = ("""
    <p><strong>Rest</strong> ist die mittlere Saisonstärke der verbleibenden Gegner, ohne
    Heimvorteil verrechnet; die Aufteilung dahinter sagt, wie viele davon zu Hause sind.</p>"""
            if has_future else "")
    return f"""<section class="method" id="methodik">
    <h2>Erklärungen</h2>
    <p class="sub">Wie jede Zahl auf dieser Seite zustande kommt – für alle, die es genau
    wissen wollen.</p>

    <h3>Form und Saison</h3>
    <p>Beide Werte sind eine Elo-Wertung: Jedes Team startet bei 1500 Punkten, nach jedem Spiel
    wandern Punkte vom Verlierer zum Gewinner. Wie viele, hängt von drei Dingen ab – wie
    überraschend das Ergebnis nach beiden Wertungen war, wie hoch es ausfiel (logarithmisch
    gedämpft, ein 6:0 zählt nicht dreimal so viel wie ein 2:0) und ob das Heimteam gewonnen hat,
    das mit einem Heimvorteil von 100 Punkten rechnet. Die Wertung wird für die Anzeige auf eine
    Skala von 0 bis 100 umgerechnet, 50 ist Ligadurchschnitt.</p>
    <p><strong>Saison</strong> läuft über alle bisherigen Spiele, wird aber nach wenigen Spielen
    Richtung 50 gedrückt: Ein Team mit drei Siegen aus drei Spielen ist nicht doppelt so stark
    wie eines mit einem Sieg. Deshalb liegt die ganze Liga nach fünf Spieltagen eng beieinander –
    das ist richtig, nicht kaputt. <strong>Form</strong> rechnet dieselbe Wertung nur über die
    letzten {FORM_WINDOW} Spieltage, jedes Mal neu von 1500 aus und ohne diese Dämpfung: Sie
    beschreibt, was zuletzt war, und beansprucht nicht, die Stärke zu messen. Gemessen an der
    Vorsaison sagt sie den nächsten Spieltag nicht besser voraus als die Tabelle – deshalb
    steht sie auf der Seite als Beschreibung, nicht als Prognose.</p>

    <h3>Formtabelle</h3>
    <p>Sortiert nach der Form, nicht nach der Saison. <strong>+/&minus;</strong> ist die
    Veränderung der Form gegenüber dem letzten Spieltag. Die Punkte unter dem Teamnamen sind
    dieselben fünf Spiele, chronologisch von links nach rechts: grün Sieg, grau Unentschieden,
    rot Niederlage. <strong>Tabelle</strong> ist der offizielle Platz; der Wert dahinter ist die
    Differenz zum Platz in dieser Formtabelle – <span class="chip up">+2</span> heißt, das Team
    steht hier zwei Plätze besser als in der Tabelle, spielt also gerade über seinem
    Saisonstand.</p>{rest}
    <p>Zwei Marker, beide nach fester Regel: <strong>Mannschaft der Stunde</strong> steht beim
    Team mit dem größten Vorsprung dieser Formtabelle auf den eigenen Tabellenplatz, mindestens
    zwei Plätze. <strong>Formsprung</strong> steht beim größten Zugewinn gegenüber dem letzten
    Spieltag, mindestens 1,5 Punkte, und nie auf derselben Zeile. Sind die Abstände kleiner,
    bleiben die Marker weg – ein Platz oder ein halber Punkt liegt im Zufall.</p>

    <h3>Überraschung des Spieltags</h3>
    <p>Dieselbe Prognose wie im Reiter Prognose, nur aus den Ergebnissen bis zu diesem
    Spieltag: der Sieg, dem sie vorher die kleinste Chance gab, wenn sie unter
    {pct(SURPRISE_MAX_P)} lag. Unentschieden zählen nicht, weil das Remis hier in jeder
    Paarung die unwahrscheinlichste Variante ist – sonst wäre fast jede Woche ein 1:1 die
    Überraschung. Dieselbe Regel markiert die Überraschungen im Reiter Team im Detail.</p>

    <h3>Belag und Heimbonus</h3>
    <p>Aus den Spielberichten: Tore pro Spiel und Heimbonus je Belag, über alle Saisons
    gerechnet, weil eine allein zu dünn ist. <strong>Heimbonus</strong> ist der Unterschied
    zwischen dem, was die Heimteams tatsächlich geholt haben, und dem, was die Stärke beider
    Teams ohne Heimvorteil erwarten ließe, in Prozentpunkten. Dass Kunstrasenplätze mehr
    Heimbonus zeigen, könnte auch daran liegen, dass die stärkeren Vereine auf Kunstrasen
    spielen – die Rechnung zieht das ab, aber der Rest ist bei dieser Datenmenge ein Hinweis
    und kein Beweis.</p>

    <h3>Rückstand und Führung</h3>
    <p>Aus den Torminuten dieser Saison. <strong>Comeback-Team</strong> ist das Team mit den
    meisten Punkten aus Spielen, in denen es zurücklag, <strong>Führung verspielt</strong> das
    mit den meisten Punkten, die es aus eigener Führung noch hergegeben hat. Beide Marker
    brauchen mindestens vier Punkte, also mehr als einen Sieg; bei Gleichstand gewinnt das Team,
    das dafür weniger Spiele brauchte. Es sind Zählwerte, keine Quoten. Darunter steht, wie
    oft das Team mit dem ersten Tor am Ende gewinnt – der Maßstab, an dem ein Comeback zu
    lesen ist.</p>

    <h3>Team im Detail</h3>
    <p><strong>Siegchance vorher</strong> ist die Prognose für dieses Spiel, gerechnet nur aus
    den Ergebnissen bis dahin, aus Sicht dieses Teams. Als <strong>Überraschung</strong> gilt
    ein Sieg unter {pct(SURPRISE_MAX_P)}, dieselbe Regel wie oben. <strong>Stärke</strong> bei
    den verbleibenden Gegnern ist deren Saisonwert. Der Saisonverlauf zeigt den Saisonwert nach
    jedem Spieltag; die anderen Teams bleiben grau zum Vergleich.</p>

    <h3>Prognose</h3>
    <p>Aus allen bisherigen Ergebnissen bekommt jedes Team eine Angriffs- und eine
    Abwehrstärke. Daraus folgt, wie viele Tore beide Seiten in dieser Paarung im Schnitt
    erzielen – Heimvorteil eingerechnet –, und aus dem Abstand zwischen beiden werden die drei
    Prozentwerte. Diese Umrechnung ist an der kompletten Saison {SEASON_PREVIOUS} geeicht, nicht
    geschätzt. <strong>Tore erwartet</strong> ist die Summe für beide Mannschaften, also eher
    ein Hinweis auf offenes Spiel oder Abtasten als auf den Sieger. <strong>Topspiel</strong>
    markiert die Paarung mit der besten gemeinsamen Form beider Teams – eine Auszeichnung nach
    der Formtabelle, keine Aussage über den Ausgang.</p>
    <p>Zwei Dinge fallen auf und sind beide richtig so. Ein <strong>Remis ist nie der
    wahrscheinlichste Ausgang</strong>, obwohl rund jedes sechste Spiel remis endet – für ein
    Unentschieden müssen beide Seiten dieselbe Zahl treffen, jede einzelne Torzahl ist
    unwahrscheinlicher als „irgendein Sieg“. Und die <strong>Prozente liegen eng
    beieinander</strong>, auch wenn ein Team klar stärker eingeschätzt wird. Das ist gemessen
    und nicht gedämpft: Ein ganzes Tor Vorsprung in der Erwartung verschiebt die Siegchance in
    dieser Liga nur um rund sechs Prozentpunkte, weil die Ergebnisse hier zu stark streuen, um
    mehr herzugeben.</p>
    <p><strong>Bilanz</strong> zählt, wie oft der wahrscheinlichste Ausgang eingetreten ist,
    und rechnet daneben einen Fehlerwert (kleiner ist besser), der auch die Höhe der Prozente
    bewertet. Verglichen wird mit der stur getippten Liga-Quote – „meistens gewinnt das
    Heimteam“ –, dem ehrlichen Nullpunkt. Nichts davon wird gespeichert: Jede Prognose folgt
    allein aus den Ergebnissen davor, die Bilanz wird jedes Mal aus dem Spielplan neu gerechnet.
    Über die Vorsaison lag die Trefferquote bei 57&nbsp;%.</p>

    <h3>Saisonausblick</h3>
    <p>Die verbleibenden Spiele werden {SIM_RUNS}-mal mit denselben erwarteten Toren wie in der
    Prognose durchgespielt und die entstehenden Tabellen gezählt. Die Stärken sind dabei die
    aktuelle Schätzung und werden selbst nicht variiert, die Streuung ist also wenn überhaupt zu
    eng. Früh in der Saison ist diese Schätzung meist der Ligadurchschnitt, die Prozente
    wiederholen dann die Tabelle und die verbleibenden Spiele. Ein Team unter 1&nbsp;% ist nicht
    bei null.</p>

    <h3>Was besser sein müsste</h3>
    <p>Die Tabelle dazu steht am Ende dieser Seite. Jeder Ansatz wurde mit demselben Verfahren in Wahrscheinlichkeiten umgerechnet und an der
    Saison {SEASON_PREVIOUS} ab Spieltag {SKIP_MATCHDAYS + 1} nachgerechnet.
    <strong>Fehler</strong> ist der mittlere Prognosefehler über alle Spiele, kleiner ist
    besser. <strong>Vorsprung</strong> ist der Abstand zur Liga-Quote in Tausendsteln, mit dem
    Standardfehler dahinter. Kein einziger Ansatz erreicht zwei Standardfehler: Keiner ist
    nachweisbar besser als diese simple Auskunft. Auch die Reihenfolge in der Tabelle ist selbst
    Zufall – rechnet man die Eichung strenger, tauschen die Zeilen die Plätze. Deshalb ist die
    Prognose ein Blickwinkel und kein Tipp, und deshalb bleibt die Form auf dieser Seite eine
    Beschreibung.</p>

    <h3>Was sich nicht messen lässt</h3>
    <p>fussball.de veröffentlicht für Amateurligen nur Ergebnisse, Torminuten, Karten, Anstoß,
    Platz und Zuschauer. Wer ein Spiel dominiert hat, wer verletzt fehlt, wie der Platz oder das
    Wetter war – dafür gibt es keine Daten, und diese Seite baut dafür auch keine Ersatzwerte.</p>
  </section>
"""


def render(season, rows, matchday_n=None):
    """Render the page for a given matchday or current state.

    Args:
        season: season id (2026/27 etc)
        rows: all rows from matches.csv (played + scheduled)
        matchday_n: if given, render as a historical snapshot of that matchday;
                    if None, render current state (all played, load scheduled from CSV)
    """
    played, scheduled, matchday = snapshot(rows, matchday_n)

    if scheduled is None:
        # Current state: load scheduled fixtures
        scheduled = load(season, "scheduled")

    has_future = bool(scheduled)

    # Calculate asset root path based on where this page will be written.
    # Current season (index.html or spieltag-NN/): depends on matchday_n
    # Archive (2025-26/index.html): one level deep
    if season != SEASON_CURRENT:
        # Archive season: docs/<season-slug>/index.html
        root = "../"
    elif matchday_n is not None:
        # Snapshot: docs/2026-27/spieltag-NN/index.html
        root = "../../"
    else:
        # Current page: docs/index.html
        root = ""

    table, matchdays = build_table(played, scheduled)
    if matchday_n is None:
        matchday = matchdays[-1] if matchdays else 0
    # else: use matchday from snapshot() - already set
    # The nav needs how far the season has actually got, not how far this
    # snapshot's own played rows reach - matchdays[-1] on a snapshot is always
    # matchday_n itself, which would hide every later matchday from the nav.
    all_played = [r for r in rows if r["status"] == "played"]
    season_max_matchday = max((int(r["matchday"]) for r in all_played), default=0)
    last_date = max(r["date"] for r in played) if played else None
    last_date = ".".join(reversed(last_date.split("-")))
    generated = date.today().strftime("%d.%m.%Y")

    top_team, bottom_team = table[0], table[-1]

    # Two markers on the table, each by a fixed rule so a reader can check them
    # against the row they sit on. Both have a floor: one place of difference or
    # a form change of half a point is well inside the noise this page keeps
    # warning about, and a badge would sell it as a story.
    # The badge only ever marks a team playing above its table place.
    best_gap, hot_rank = max((t["position"] - (rank + 1), rank) for rank, t in enumerate(table))
    hot_rank = hot_rank if best_gap >= 2 else None
    # The same team, and the same chip, in the head - so the third fact up there
    # and the badge down in the table can never disagree.
    if hot_rank is None:
        hot_fact = '<span class="t none">diesen Spieltag keine</span>'
    else:
        hot_fact = (f'<span class="t">{html.escape(table[hot_rank]["team"])}</span>'
                    f'<span class="chip up">+{best_gap}</span>')
    jumps = [(t["delta"], rank) for rank, t in enumerate(table) if t["delta"] is not None]
    best_delta, best_rank = max(jumps, default=(0, None))
    jump_rank = best_rank if best_delta >= 1.5 and best_rank != hot_rank else None

    logos = load_logos(root)
    pitch_svg, pitch_lo, pitch_hi = svg_pitch(table, logos)

    # Team detail panels: only render for current season
    # and generate HTML for each team's detail section
    if season == SEASON_CURRENT:
        match_probs = match_probabilities(played, season)
        team_panels_html = team_details_html(table, played, logos, season, matchdays, match_probs)
    else:
        team_panels_html = ""

    # Forecast and season outlook side by side; both share the same gate
    # (MIN_MATCHES), so one never renders without the other. The measurement
    # of what the forecast is worth ships on every page, under the
    # explanations - the page is not allowed to publish a forecast without it.
    fcast = forecast_section(season, played, logos, {t["team"]: t["form"] for t in table}, scheduled)
    predictors = f'<div class="fdash solo">{predictor_section()}</div>'
    sim_section = simulation_section(season, played, logos, scheduled)
    outlook = (f'<div class="dash fdash"><div class="col">{fcast}</div>'
               f'<div class="col">{sim_section}</div></div>')

    # One tab per question, and the explanations last. An archive page has no
    # team panels and nothing to forecast, so it has no forecast tab.
    views = [("form", "Form", None)]
    if team_panels_html:
        views.append(("team", "Team im Detail", team_panels_html))
    if fcast:
        views.append(("prognose", "Prognose", outlook))
    views.append(("methodik", "Erklärungen", method_section(has_future) + predictors))
    tabs_html = "  <div class=\"tabs\" role=\"tablist\">\n" + "".join(
        f'    <button role="tab" id="t-{vid}" aria-controls="v-{vid}" '
        f'aria-selected="{"true" if vid == "form" else "false"}" data-view="{vid}">{label}</button>\n'
        for vid, label, _ in views) + "  </div>\n"
    form_view_attrs = ' role="tabpanel" aria-labelledby="t-form"'
    other_views = "".join(
        f'  <div class="view" id="v-{vid}" role="tabpanel" aria-labelledby="t-{vid}" hidden>\n'
        f'{content}\n  </div>\n' for vid, _, content in views[1:])

    body_rows = []
    for rank, t in enumerate(table):
        w, d, l = t["record"]
        if t["delta"] is None:
            delta = '<span class="flat">–</span>'
        else:
            sign = "+" if t["delta"] >= 0 else "−"
            cls = "up" if t["delta"] > 0.05 else ("down" if t["delta"] < -0.05 else "flat")
            delta = f'<span class="{cls}">{sign}{num(abs(t["delta"]))}</span>'
        # Positive: the team is playing better than its table place suggests.
        diff = t["position"] - (rank + 1)
        if diff == 0:
            gap = '<span class="chip flat">±0</span>'
        else:
            cls = "up" if diff > 0 else "down"
            gap = f'<span class="chip {cls}">{"+" if diff > 0 else "−"}{abs(diff)}</span>'
        segs = "".join(f'<i class="{r}"></i>' for r in t["dots"])
        zone = " top" if rank < 3 else (" bottom" if rank >= len(table) - 3 else "")
        tok = f'<span class="tok{zone}">{rank + 1}</span>'
        crest = logos.get(team_slug(t["team"]))
        crest = f'<img class="lg" src="{crest}" alt="">' if crest else ""
        if rank == hot_rank:
            # The highlighter across the row does the pointing for this one.
            badge = '<span class="badge">Mannschaft der Stunde</span>'
        elif rank == jump_rank:
            badge = f'<span class="badge">Formsprung{ARROW}</span>'
        else:
            badge = ""

        # The badge sits beside the name block rather than inside it, and CSS
        # takes it out of flow: writing on a printed sheet cannot move what was
        # printed first, so an annotation must not change a row's height.
        # Only the team of the hour gets the highlighter on top of its note; the
        # form jump is the smaller finding and stays a written remark, so the
        # two markers are told apart by how loudly they are marked.
        rest_html = rest_cell(t.get("remaining")) if has_future else ""
        body_rows.append(
            f'<tr class="{"marked" if rank == hot_rank else ""}">'
            f'<td class="rank">{tok}</td>'
            f'<td class="team"><div class="tc">{crest}<div>'
            f'<span class="tn">{html.escape(t["team"])}</span>'
            f'<span class="form"><span class="seg" aria-hidden="true">{segs}</span>'
            f'<span class="rt">{w}-{d}-{l}</span></span></div></div>{badge}</td>'
            f'<td class="power"><div class="pw"><b>{num(t["form"])}</b></div></td>'
            f"<td>{delta}</td>"
            f'<td class="power season">{num(t["power"])}</td>'
            f"<td class=\"s-hide\">{t['matches']}</td>"
            f"<td class=\"s-hide\">{t['gf']}:{t['ga']}</td>"
            f"<td class=\"s-hide\">{signed(t['gf'] - t['ga'])}</td>"
            f"{rest_html}"
            f"<td class=\"tab\">{t['position']}{gap}</td>"
            f"</tr>"
        )

    # The right column: on the current season the matchday's surprise, on an
    # archive page the form chart - a finished season has no "this weekend",
    # and its forecast calibration (the season before it) is not loaded.
    if season == SEASON_CURRENT:
        side_section = f"""<h2>Überraschung des Spieltags</h2>
        <p class="sub">Der Sieg dieses Spieltags, dem die Prognose vorher die geringste Chance gab.
        Unentschieden zählen nicht.</p>
        {surprise_card(played, table, matchday, logos)}"""
        # Two more cards from the match detail pages, both absent on a clone
        # without data/details.csv rather than rendered empty.
        surface, marks, first = page_data(played, season)
        side_section += f"""
        {surface_section(surface)}
        {comeback_section(marks, first, logos)}"""
    else:
        side_section = f"""<h2>Formverlauf</h2>
        <p class="sub">Die Form an jedem Spieltag, also immer das Fenster der fünf davor.
        Diese Linien springen – das ist gewollt, sie zeigen Phasen und keine Bilanz.</p>
        <div class="card chart">{svg_chart(table, matchdays, "fseries", "form", range(3))}</div>
        <p class="hint">X-Achse: Spieltag, Y-Achse: Form. Die drei formstärksten Teams sind
        farbig, die anderen grau.</p>"""

    # Only render Rest column header when there are scheduled fixtures
    rest_header = '<th class="s-hide">Rest</th>' if has_future else ""

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Power Ranking – {html.escape(STAFFEL_NAME)} {season}</title>
<style>
  {font_face("Archivo Narrow", "archivo-narrow.woff2", "400 700", root)}
  {font_face("Source Sans 3", "source-sans-3.woff2", "300 700", root)}
  {font_face("Caveat", "caveat.woff2", "500 700", root)}
  :root {{
    /* Two faces, far apart on purpose. The narrow print grotesque carries every
       figure, heading and table head - this is a page about one number per team,
       so the numbers get the voice. The humanist sans carries club names and the
       long plain-language passages, where width and openness matter more. */
    --display:"Archivo Narrow","Arial Narrow",system-ui,sans-serif;
    --body:"Source Sans 3","Segoe UI",system-ui,sans-serif;
    /* Only the two editorial markers use this - the bits of the sheet a person
       wrote rather than the model computed. Subset to letters and a space, so
       a badge label with a digit in it would fall back to the display face. */
    --hand:"Caveat","Segoe Script",cursive;

    /* Uncoated stock and printing ink: this is a notice pinned up after the
       weekend, not a broadcast graphic. */
    --paper:#e7e4dd; --card:#fcfbf8; --ink:#23201b; --muted:#6b6459;
    --line:#d8d3c8; --track:#dcd7cc;
    /* Above and below the 50-point league average. These two mean the same
       thing everywhere on the page and are the one thing that must not drift. */
    --up:#16764f; --down:#96263f; --draw:#a9a294;
    /* The pitch is the only dark surface on the page, and it is grass rather
       than a dark UI panel - so both meanings need a lifted variant on it. */
    --turf:#1f3129; --chalk:rgba(255,255,255,.26); --on-turf:#a7bdaf;
    --up-l:#63d9a6; --down-l:#f2808f;
    /* Editorial markers only, in a colour no measurement uses: amber ink for
       the written notes, a highlighter yellow for the rows they point at. */
    --mark:#7a4a09; --mark-bg:#f6e2bc; --mark-line:#e6cb96;
  }}
  * {{ box-sizing:border-box; }}
  /* The stock itself, then what has happened to it. Top three layers are four
     coffee marks in fixed places, so the sheet looks the same from one matchday
     to the next; under them the sparse dark flecks of recycled paper and a fine
     fibre grain, both drawn by the browser rather than shipped as images. All
     of it sits on the ground only - every card and the pitch are opaque. */
  body {{ margin:0; background-color:var(--paper); color:var(--ink);
         font:16.5px/1.62 var(--body); -webkit-font-smoothing:antialiased;
         background-repeat:no-repeat, no-repeat, no-repeat, no-repeat,
                           repeat, repeat;
         background-size:186px 168px, 118px 112px, 148px 132px, 96px 92px,
                         240px 240px, 190px 190px;
         background-position:5% 2.4%, 93% 5%, 86% 71%, 11% 92%, 0 0, 0 0;
         background-image:
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,0) 0 43%,
             rgba(122,84,40,.075) 45% 49%, rgba(122,84,40,.03) 50.5% 54%,
             rgba(122,84,40,0) 56%),
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,0) 0 45%,
             rgba(122,84,40,.06) 47% 51%, rgba(122,84,40,0) 53%),
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,.032) 0 34%,
             rgba(122,84,40,.05) 44% 48%, rgba(122,84,40,0) 51%),
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,.045) 0 30%,
             rgba(122,84,40,0) 64%),
           url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='240'%3E%3Cfilter id='f'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='1.1' numOctaves='1' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3CfeComponentTransfer%3E%3CfeFuncA type='linear' slope='7' intercept='-4.85'/%3E%3C/feComponentTransfer%3E%3C/filter%3E%3Crect width='240' height='240' filter='url(%23f)' opacity='0.5'/%3E%3C/svg%3E"),
           url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='190' height='190'%3E%3Cfilter id='p'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.55 1.3' numOctaves='5' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='190' height='190' filter='url(%23p)' opacity='0.115'/%3E%3C/svg%3E"); }}
  .wrap {{ max-width:1760px; margin:0 auto; padding:0 clamp(16px,2.4vw,36px); }}
  .prose {{ max-width:66ch; }}
  p {{ margin:0 0 12px; }}
  strong, b {{ font-weight:600; }}
  a {{ color:inherit; text-underline-offset:2px; }}
  a:focus-visible {{ outline:2px solid currentColor; outline-offset:3px; }}
  .line polyline {{ transition:stroke .12s ease, stroke-width .12s ease; }}
  @media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}

  /* ---- The token: one numbered disc in plain ink, coloured only at the ends
     of the form table - the top three and the bottom three. Zones of this
     table, not the side of the average the rest of the page colours by. */
  .tok {{ display:inline-flex; align-items:center; justify-content:center;
          width:27px; height:27px; border-radius:50%; flex:0 0 auto;
          font:700 14px var(--display); color:var(--paper); background:var(--ink);
          font-variant-numeric:tabular-nums; }}
  .tok.top {{ background:var(--up); }}
  .tok.bottom {{ background:var(--down); }}

  /* ---- Masthead ----------------------------------------------------------- */
  /* No dark hero band: the sheet starts on paper and the heavy rule under the
     wordmark is what says "this is the head of the notice". */
  .mast {{ padding:22px 0 0; }}
  .mhead {{ display:flex; flex-wrap:wrap; align-items:flex-end; gap:10px 32px;
            justify-content:space-between; padding-bottom:10px;
            border-bottom:3px solid var(--ink); }}
  h1 {{ margin:0; font:700 clamp(30px,3.9vw,50px)/1 var(--display); }}
  .tag {{ margin:7px 0 0; color:var(--muted); font-size:15px; max-width:74ch; }}
  .where {{ margin:0; color:var(--muted); font-size:14.5px; line-height:1.4;
            text-align:right; }}
  .where b {{ color:var(--ink); font-weight:600;
              font-variant-numeric:tabular-nums; }}
  .seasons {{ display:flex; gap:14px; flex-wrap:wrap; padding:10px 0;
             font-size:14.5px; }}
  .seasons span {{ color:var(--ink); font-weight:600; }}
  .seasons a {{ color:inherit; }}

  /* Matchday navigation: numbers 1..N with links to snapshots, past matches are clickable */
  .matchdays {{ display:flex; gap:8px; align-items:center; padding:8px 0;
               font-size:13px; }}
  .matchdays a {{ color:var(--ink); text-decoration:none; padding:2px 6px;
                 border:1px solid var(--line); border-radius:3px; }}
  .matchdays a:hover {{ background-color:var(--card); }}
  .matchdays span[aria-current] {{ font-weight:600; color:var(--down); }}
  .matchdays span.future {{ color:var(--muted); }}
  .matchdays .nav-prev, .matchdays .nav-next {{ padding:2px 4px; }}

  /* Banner for snapshot pages showing which matchday this is. */
  .banner.snapshot {{ background-color:var(--mark-bg); padding:12px; margin:0;
                      border-bottom:2px solid var(--mark-line); }}
  .banner.snapshot p {{ margin:0; font-size:14px; color:var(--mark); }}
  .banner.snapshot .current-link {{ color:var(--down); text-decoration:none; }}
  .banner.snapshot .current-link:hover {{ text-decoration:underline; }}

  /* Three facts of equal weight in one row: the two ends of the form table and
     the team the table is most wrong about. Same size, same build, so none of
     them reads as a title over the others. */
  .facts {{ display:flex; flex-wrap:wrap; gap:14px 48px; padding:20px 0 22px; }}
  /* Label on the first line, name and value side by side on the second. */
  .fact {{ display:grid; grid-template-columns:auto auto; justify-content:start;
           align-items:baseline; column-gap:9px; row-gap:3px; margin:0; }}
  .fact .k {{ grid-column:1 / -1; color:var(--muted); font-size:14.5px; }}
  .fact .t {{ font-weight:600; font-size:17px; }}
  .fact .t.none {{ font-weight:400; color:var(--muted); }}
  .fact .n {{ font:700 21px var(--display); font-variant-numeric:tabular-nums; }}
  .fact .n.pos {{ color:var(--up); }} .fact .n.neg {{ color:var(--down); }}
  .fact .chip {{ margin-left:0; font-size:14px; padding:2px 8px; }}

  /* ---- Body -------------------------------------------------------------- */
  main {{ padding:0 0 60px; }}
  h2 {{ font:600 22px/1.15 var(--display); margin:28px 0 8px; padding-top:9px;
        border-top:2.5px solid var(--ink); }}
  section > h2:first-child {{ margin-top:0; }}
  .sub {{ color:var(--muted); font-size:14.5px; margin:-1px 0 13px; max-width:66ch; }}

  /* The pitch runs across the full page width - it is the one view that shows
     the whole league at once, and the tokens need the room. */
  .pitchsec {{ margin:0 0 6px; }}

  /* The dashboard below it: table on the left, the side cards on the right. */
  .dash {{ display:grid; grid-template-columns:1fr; gap:26px; align-items:start; }}
  .col section + section {{ margin-top:22px; }}

  .card {{ background:var(--card); border:1px solid var(--line); border-radius:3px;
           padding:0; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse;
           font:15.5px var(--display); font-variant-numeric:tabular-nums; }}
  th, td {{ padding:9px 8px; text-align:right; border-bottom:1px solid var(--line); }}
  th:first-child, td:first-child {{ padding-left:14px; }}
  th:last-child, td:last-child {{ padding-right:14px; }}
  tbody tr:last-child td {{ border-bottom:0; }}
  /* A ruled head rather than a dark bar: the whole page is a printed sheet, and
     the double rule is how a sheet separates the head from the entries. */
  thead th {{ font:600 13.5px var(--display); color:var(--ink); white-space:nowrap;
              border-bottom:2px solid var(--ink); padding-top:11px;
              padding-bottom:7px; }}
  th.l, td.team, td.power {{ text-align:left; }}
  td.rank {{ width:46px; }}
  td.team {{ line-height:1.3; min-width:232px; }}
  .tc {{ display:flex; align-items:center; gap:10px; }}
  .lg {{ width:28px; height:28px; object-fit:contain; flex:0 0 auto; }}
  /* A club name that wraps makes its row taller than every other one, which is
     exactly the ragged scan the form table exists to avoid. It keeps one line
     and widens its column instead; the card already scrolls if that is too
     much. The phone rule below hands wrapping back, where width is the scarce
     thing rather than rhythm. */
  .tn {{ display:block; font-family:var(--body); font-weight:600; font-size:15.5px;
         white-space:nowrap; }}
  /* nowrap so an annotation too long for the cell runs out of it rather than
     dropping onto a line of its own; the phone rule below puts wrapping back. */
  .form {{ display:flex; align-items:center; flex-wrap:nowrap; gap:4px 8px; margin-top:2px; }}
  /* The one bold thing on the page: the form figure, set large in the narrow
     face. Everything around it stays quiet. */
  .pw b {{ font:700 23px var(--display); }}

  .seg {{ display:flex; gap:4px; flex:0 0 auto; align-items:center; }}
  .seg i {{ width:7px; height:7px; border-radius:50%; flex:0 0 auto; }}
  .seg i.w {{ background:var(--up); }}
  .seg i.d {{ background:var(--draw); }}
  .seg i.l {{ background:var(--down); }}
  .rt {{ color:var(--muted); font-size:13px; }}
  .rs {{ display:block; color:var(--muted); font-size:12.5px; margin-top:2px; }}

  /* The row an editorial marker points at, gone over with a highlighter. Two
     strokes, neither of them the width of the row - a marker starts and stops
     where the hand stops, and the second pass never lands on the first.
     Background images rather than positioned pseudo-elements: `position:
     relative` on a <tr> throws off column widths under `border-collapse:
     collapse`, which squeezed the marked row's cells to a fraction of the
     others. The wobble is in the SVG outline, the uneven pressure in its
     gradient, and a background paints under the cell content for free. */
  tr.marked {{ background-repeat:no-repeat, no-repeat;
            background-size:63% 64%, 46% 42%;
            background-position:2% 56%, 10% 88%;
            background-image:{STROKE_A}, {STROKE_B}; }}

  .up {{ color:var(--up); }} .down {{ color:var(--down); }} .flat {{ color:var(--muted); }}
  td.tab {{ white-space:nowrap; }}
  /* Square: a chip is a measured difference. Badges below are stamps and stay
     rounded, so the two never read as the same kind of thing. */
  .chip {{ display:inline-block; margin-left:6px; padding:1px 6px; border-radius:2px;
           font:600 12.5px var(--display); }}
  .chip.up {{ background:#d6e8de; color:#0e5c3d; }}
  .chip.down {{ background:#f2dbe0; color:#7d1f34; }}
  .chip.flat {{ background:#e4e0d6; color:var(--muted); }}

  /* Editorial marker, deliberately in a colour no data uses: green and wine
     mean above and below average everywhere else on the page, and a badge is
     not a measurement. Set as a pen annotation for the same reason - a ring
     drawn round a row by hand cannot be mistaken for something the model
     computed. The lopsided radii are what make the ring look drawn; they scale
     with the label instead of distorting the way a stretched drawing would.
     Taken out of flow entirely and anchored to the cell rather than laid out
     in it: an annotation written onto a sheet cannot push the print around, so
     it must not change a row's height or a column's width. It is free to run
     over the rule below it and across the cell next door. */
  td.team, td.fx {{ position:relative; }}
  .badge {{ position:absolute; z-index:5; left:150px; bottom:-3px;
            padding:2px 13px 3px; white-space:nowrap;
            font:700 16px/1.2 var(--hand);
            color:var(--mark); transform:rotate(-4.2deg); }}
  /* Two overlapping ovals, because that is how a ring round something on paper
     actually comes out - one pass never closes. Percentage radii keep it an
     oval at any label length instead of a rounded box. */
  .badge::before, .badge::after {{ content:""; position:absolute; inset:0;
            border:1.5px solid rgba(122,74,9,.5);
            border-radius:47% 53% 44% 56%/62% 58% 42% 38%; }}
  .badge::after {{ border-radius:53% 47% 57% 43%/45% 40% 60% 55%;
            transform:rotate(1.3deg) scale(1.035); opacity:.5; }}
  /* Drawn outside the ring and clear of the digits, so the note reaches the
     column it is about without covering anything measured. */
  .arw {{ position:absolute; left:100%; top:-3px; width:46px; height:21px;
          margin-left:3px; overflow:visible; }}
  .arw path {{ fill:none; stroke:rgba(122,74,9,.62); stroke-width:1.9;
               stroke-linecap:round; stroke-linejoin:round; }}

  /* ---- Forecast and the predictor comparison ------------------------------ */
  /* The two share one row of the same grid the dashboard uses: publishing a
     forecast is only defensible next to the measurement of how little it is
     worth, and side by side that is shown rather than only written down. */
  .fdash {{ margin-top:34px; }}
  .fdash.solo {{ max-width:1080px; }}
  .fcast td.l {{ text-align:left; }}
  .fcast td.dt {{ color:var(--muted); font-size:13.5px; white-space:nowrap; }}
  .fcast td.p {{ width:64px; }}
  .fcast td.best {{ font-weight:700; }}
  .fcast td.xg, .fcast td.ld {{ color:var(--muted); white-space:nowrap; }}
  .fcast tr.base > td {{ border-top:2px solid var(--ink); }}
  .fcast td.l:first-child + td {{ font-family:var(--body); }}
  .fcast .pm {{ font-size:12.5px; }}
  /* The highlighter carries the marking here, so no tinted row underneath it,
     and the strokes get their own lengths - two rows marked identically would
     look stamped rather than written. */
  .fcast tr.hl > td:first-child {{ box-shadow:inset 3px 0 0 var(--mark); }}
  .fcast tr.marked {{ background-size:56% 58%, 38% 34%;
            background-position:3% 34%, 14% 78%; }}
  /* In the empty right half of the fixture cell, clear of both club names. */
  .fx .badge {{ left:auto; right:5%; bottom:auto; top:50%;
                transform:translateY(-50%) rotate(-4.8deg); }}
  .fxt {{ display:flex; align-items:center; gap:8px; line-height:1.3; }}
  .fxt span {{ font-weight:600; }}
  .fxt + .fxt {{ margin-top:3px; }}
  .lg.sm {{ width:21px; height:21px; }}
  /* Same three colours as the result dots, and the same meaning: seen from the
     home side, win / draw / loss. */
  .bar {{ display:flex; height:5px; max-width:280px; margin-top:7px;
          overflow:hidden; background:var(--track); }}
  .bar i.w {{ background:var(--up); }}
  .bar i.d {{ background:var(--draw); }}
  .bar i.l {{ background:var(--down); }}

  /* ---- The pitch panel ---------------------------------------------------- */
  /* Grass, with the mown bands the stripes on a real pitch make - the one place
     on the page where a texture depicts the actual object. */
  /* Four layers, front to back: a grain so the green is not a flat fill, a
     vignette for the fall-off a real pitch has towards the touchlines, the fine
     lines the mower leaves, and the wide mown bands across the pitch. */
  .pitchcol {{ background-color:var(--turf); border-radius:3px; padding:11px 14px 10px;
               background-repeat:repeat, no-repeat, repeat, repeat;
               background-size:150px 150px, 100% 100%, 100% 100%, 100% 100%;
               background-image:
                 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='150' height='150'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='150' height='150' filter='url(%23g)' opacity='0.13'/%3E%3C/svg%3E"),
                 radial-gradient(130% 155% at 50% 50%, rgba(0,0,0,0) 36%,
                   rgba(0,0,0,.32) 100%),
                 repeating-linear-gradient(0deg, rgba(255,255,255,.017) 0 4px,
                   rgba(255,255,255,0) 4px 9px),
                 repeating-linear-gradient(90deg, rgba(255,255,255,.055) 0 58px,
                   rgba(0,0,0,.07) 58px 116px); }}
  .pwrap {{ position:relative; }}
  .pwrap svg {{ width:100%; height:auto; display:block; }}
  .pg rect, .pg circle {{ fill:none; stroke:var(--chalk); stroke-width:1.6; }}
  .half {{ stroke:var(--chalk); stroke-width:1.6; }}
  .stick {{ stroke:rgba(255,255,255,.19); stroke-width:1.4; }}
  .axis {{ stroke:rgba(255,255,255,.15); stroke-width:1; stroke-dasharray:3 5; }}
  .ptok path {{ stroke:var(--turf); stroke-width:2.5; stroke-linejoin:round; }}
  .ptok path.pos {{ fill:var(--up-l); }}
  .ptok path.neg {{ fill:var(--down-l); }}
  /* The crest sits on a white chest patch, so the shirt colour still reads as
     the above/below-average marker the legend explains. */
  .ptok circle.in {{ fill:#fff; }}
  .ptok circle.rkb {{ fill:var(--turf); stroke:#fff; stroke-width:1.5; }}
  .ptok text {{ fill:var(--turf); font:700 23px var(--display); text-anchor:middle; }}
  .ptok text.rk {{ fill:#fff; font-size:12px; }}
  .pends {{ display:flex; justify-content:space-between; gap:12px; margin:0 0 5px; }}
  .pend {{ display:flex; align-items:center; gap:7px; margin:0;
           color:var(--on-turf); font-size:12.5px; }}
  .pend i {{ width:9px; height:9px; border-radius:50%; flex:0 0 auto; }}
  .pend i.pos {{ background:var(--up-l); }} .pend i.neg {{ background:var(--down-l); }}
  .pend b {{ color:#fff; font:700 15px var(--display);
             font-variant-numeric:tabular-nums; }}
  .phead {{ margin:9px 0 0; padding-top:8px; color:var(--on-turf); font-size:12.5px;
            line-height:1.5; border-top:1px solid rgba(255,255,255,.12); }}

  /* ---- Chart -------------------------------------------------------------- */
  .card.chart {{ padding:10px 12px; }}
  .chart svg {{ width:100%; height:auto; display:block; }}
  .zone.pos {{ fill:var(--up); opacity:.05; }}
  .zone.neg {{ fill:var(--down); opacity:.05; }}
  .grid {{ stroke:#e9e5da; stroke-width:1; }}
  .tick {{ fill:var(--muted); font:11.5px var(--display); }}
  .line polyline {{ fill:none; stroke:#d5cfc2; stroke-width:1.5; stroke-linejoin:round;
                    stroke-linecap:round; }}
  .line circle {{ display:none; }}
  .line .end {{ display:none; }}
  .line.sel polyline {{ stroke:var(--c); stroke-width:2.8; }}
  .line.sel circle {{ display:inline; fill:var(--card); stroke:var(--c); stroke-width:2; }}
  .line.sel .end {{ display:inline; }}
  .end circle {{ display:inline; stroke:var(--card); stroke-width:2.5; }}
  .end circle.pos {{ fill:var(--up); }}
  .end circle.neg {{ fill:var(--down); }}
  .end text {{ fill:#fff; font:700 13px var(--display); text-anchor:middle; }}
  /* The season score is context, not the headline: same column width, quieter. */
  td.season {{ color:var(--muted); font-weight:600; }}

  /* ---- Surprise of the matchday ------------------------------------------- */
  /* One result set like a line of the forecast table: both clubs, the score in
     the display face, the pre-match percentages as the same three-colour bar. */
  .surprise {{ padding:16px 18px; }}
  .surprise .none {{ margin:0; color:var(--muted); }}
  .smatch {{ display:grid; grid-template-columns:1fr auto; align-items:center;
             column-gap:16px; }}
  .smatch .fxt {{ grid-column:1; }}
  .smatch .fxt.win span {{ color:var(--up); }}
  .smatch .score {{ grid-column:2; grid-row:1 / span 2; font:700 34px var(--display);
                    font-variant-numeric:tabular-nums; }}
  .surprise .bar {{ max-width:none; margin-top:12px; }}
  .sprob {{ margin:6px 0 0; color:var(--muted); font-size:14px; }}
  .sprob b {{ color:var(--ink); }}
  .sfacts {{ margin-top:12px; }}
  .sfacts th, .sfacts td {{ padding:6px 5px; }}
  .sfacts td.l {{ text-align:left; font-family:var(--body); font-weight:600; }}
  .sfacts .to {{ color:var(--muted); font-weight:400; }}
  .surprise .hint {{ margin-top:12px; }}

  /* ---- Pitch surface and comebacks, under the surprise ------------------- */
  .chartcol h3 {{ margin:26px 0 8px; font:600 17px/1.2 var(--display); }}
  .belag td.l .n {{ display:block; color:var(--muted); font:400 13px var(--body); }}
  .belag tr.total > td {{ border-top:2px solid var(--ink); }}
  /* The bonus as a number and, under it, as a length - so +19 against +7 is
     seen before it is read. All bars share one scale, the longest is full. */
  .belag td.hb {{ width:112px; }}
  .belag td.hb b {{ display:block; font:700 18px var(--display); color:var(--up);
                    font-variant-numeric:tabular-nums; }}
  .hbar {{ display:block; height:4px; margin:3px 0 0 auto; width:72px; background:var(--track); }}
  .hbar i {{ display:block; height:100%; background:var(--up); }}
  .cbs {{ padding:14px 18px 16px; }}
  /* Each marked team is one line: the handwritten note in the margin, the
     club, then the sentence with the count. The note is out of flow as
     everywhere else, so the row keeps the height of its text. */
  .cb {{ position:relative; padding:6px 0 8px 142px; }}
  .cb + .cb {{ border-top:1px solid var(--line); }}
  .cb .badge {{ left:0; top:9px; bottom:auto; transform:rotate(-3.6deg); }}
  .cb p {{ margin:2px 0 0; font-size:15px; }}
  .cb p.none {{ margin:0; color:var(--muted); }}
  .cbs .first {{ margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }}
  .cbs .first p {{ margin:0; font-size:15px; }}
  .cbs .first .bar {{ max-width:none; }}

  .hint {{ color:var(--muted); font-size:14px; line-height:1.58; margin:12px 0 0;
           max-width:66ch; }}
  /* The legend for the table sits in the chart column, not under the table it
     explains: side by side the chart ran three hundred pixels short of the
     fourteen rows beside it, and a legend reads as well across the gutter as
     underneath. Ruled off so it is not taken for a note about the chart. */
  .hint.legend {{ margin-top:15px; padding-top:12px;
                  border-top:1px solid var(--line); }}
  /* The small print under the forecast, set denser and in two columns so the
     explanation does not run longer than the table it explains. */
  .cols {{ column-count:2; column-gap:46px; max-width:1000px;
           font-size:15.5px; line-height:1.58; }}
  .cols p {{ margin:0 0 13px; break-inside:avoid; }}
  .cols.hints {{ margin-top:14px; }}
  .cols .hint {{ max-width:none; margin:0 0 13px; }}

  /* The rule runs the width of the sheet, the text keeps a readable measure. */
  footer {{ margin-top:40px; padding-top:14px; border-top:2.5px solid var(--ink);
            color:var(--muted); font-size:13.5px; }}
  footer p {{ margin:0; max-width:100ch; }}

  /* ---- Tabs ---------------------------------------------------------------- */
  /* Three questions on one notice - who is in form, one team up close, what
     comes next - as index tabs on the same ink rule every heading uses. The
     open tab breaks the rule, the way a folder tab joins its own sheet; the
     others stay unfilled, so nothing here reads as a button. */
  .tabs {{ display:flex; gap:4px; margin:26px 0 0; padding:0 8px;
           border-bottom:2.5px solid var(--ink); }}
  .tabs button {{ appearance:none; cursor:pointer; margin:0 0 -2.5px;
                  padding:9px 16px 10px; font:600 16px/1 var(--display);
                  color:var(--muted); background:transparent;
                  border:2.5px solid transparent; border-radius:4px 4px 0 0; }}
  .tabs button:hover {{ color:var(--ink); }}
  .tabs button[aria-selected="true"] {{ color:var(--ink); border-color:var(--ink);
                                        border-bottom-color:var(--paper); }}
  .tabs button:focus-visible {{ outline:2px solid var(--ink); outline-offset:-6px; }}
  .view[hidden] {{ display:none; }}
  /* The tab strip already rules the view off, so the first block starts flush. */
  .tabs ~ .view > :first-child {{ margin-top:26px; }}

  /* ---- Explanations ------------------------------------------------------ */
  /* Plain running text at a readable measure, one heading per thing on the
     sheet it explains. */
  .method {{ max-width:70ch; }}
  .method h3 {{ margin:26px 0 6px; font:600 17px/1.2 var(--display); }}
  .method p {{ margin:0 0 12px; font-size:15.5px; line-height:1.6; }}
  .method + .fdash {{ margin-top:44px; }}

  /* ---- Team detail panels ------------------------------------------------ */
  .tdet h2 {{ margin-top:0; }}
  .tdet h3 {{ margin:22px 0 8px; font:600 17px/1.2 var(--display); }}
  .tdet .col > h3:first-child {{ margin-top:0; }}
  .tpanel[hidden] {{ display:none; }}
  /* The picker is a line on the sheet, not a widget: the name sits in the
     display face on an ink rule, with the browser's own arrow beside it. */
  .tpick {{ display:inline-flex; align-items:baseline; gap:10px; margin:0 0 22px;
            color:var(--muted); font-size:14.5px; }}
  .tpick select {{ appearance:none; -webkit-appearance:none; cursor:pointer;
                   font:700 20px/1.2 var(--display); color:var(--ink);
                   background:transparent; border:0; border-bottom:2.5px solid var(--ink);
                   border-radius:0; padding:2px 26px 3px 0; max-width:min(100%,24ch);
                   background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' fill='none' stroke='%2323201b' stroke-width='2'/%3E%3C/svg%3E");
                   background-repeat:no-repeat; background-position:right 4px center;
                   background-size:12px 8px; }}
  .tpick select:focus-visible {{ outline:2px solid var(--ink); outline-offset:4px; }}
  .thead {{ display:flex; gap:24px; margin:0 0 20px; }}
  .tchest {{ display:flex; gap:14px; flex:0 0 auto; }}
  .tname {{ display:block; font:700 20px var(--display); color:var(--ink); margin-bottom:4px; }}
  .tstats {{ display:flex; flex-direction:column; gap:3px; font-size:14px; }}
  .tstats span {{ display:flex; gap:8px; }}
  .tstats b {{ font-weight:700; }}
  .tstats .trank {{ color:var(--muted); }}
  .tres {{ width:100%; border-collapse:collapse; font-size:14px; }}
  .tres th, .tres td {{ padding:8px 6px; text-align:left; border-bottom:1px solid var(--line); }}
  .tres thead th {{ font:600 12.5px var(--display); color:var(--ink); border-bottom:2px solid var(--ink); }}
  .tres tbody tr:last-child td {{ border-bottom:0; }}
  .tres .md {{ width:40px; color:var(--muted); font:600 var(--display); }}
  .tres .dt {{ width:80px; color:var(--muted); }}
  .tres .opp, .tres .vs {{ font-weight:600; }}
  .tres .vs {{ color:var(--muted); font-size:12px; margin-left:3px; }}
  .tres .sc {{ width:80px; font-weight:600; white-space:nowrap; }}
  .tres .pr, .tres .pw {{ color:var(--muted); width:80px; }}
  /* Result from the team's side, in the same three colours as the form dots. */
  .res {{ display:inline-block; width:18px; height:18px; margin-right:7px; border-radius:2px;
          font:700 11.5px/18px var(--display); text-align:center; color:#fff; }}
  .res.w {{ background:var(--up); }}
  .res.d {{ background:var(--draw); color:var(--ink); }}
  .res.l {{ background:var(--down); }}
  .tdet .badge {{ position:relative; left:auto; right:auto; transform:none; }}

  /* ---- Season simulation -------------------------------------------------- */
  .sim table {{ width:100%; }}
  .sim th {{ text-align:center; }}
  .sim th.l {{ text-align:left; }}
  .sim td.l {{ display:flex; align-items:center; gap:8px; }}
  .sim td.l span {{ font-weight:600; }}
  .sim td {{ padding:8px; text-align:right; }}
  .sim .bar {{ display:flex; height:5px; max-width:180px; margin:5px 0 2px;
              overflow:hidden; background:var(--track); }}
  .sim .bar i {{ flex-grow:1; }}

  /* Two columns as soon as the table fits next to the chart without scrolling. */
  @media (min-width:1400px) {{
    /* Nine columns of table need the room more than five matchdays of chart. */
    .dash {{ grid-template-columns:minmax(0,60fr) minmax(0,40fr); gap:30px; }}
  }}
  @media (max-width:1399px) {{
    /* Stacked: keep one comfortable measure instead of stretching to 1760px. */
    .dash, .pitchsec, .fdash, .tdet {{ max-width:1080px;
                                margin-left:auto; margin-right:auto; }}
  }}
  /* Below this the two columns of small print would be forty characters wide,
     which is worse than one column of the same text. */
  @media (max-width:900px) {{
    .cols {{ column-count:1; max-width:66ch; font-size:16px; }}
  }}
  @media (max-width:700px) {{
    .s-hide {{ display:none; }}
    .mhead {{ align-items:flex-start; }}
    .where {{ text-align:left; }}
    .seasons {{ gap:10px; font-size:13px; }}
    .facts {{ gap:12px 26px; padding:13px 0 24px; }}
    th, td {{ padding:9px 5px; }}
    th:first-child, td:first-child {{ padding-left:10px; }}
    th:last-child, td:last-child {{ padding-right:10px; }}
    td.rank {{ width:38px; }}
    td.team {{ min-width:0; }}
    .tn {{ white-space:normal; }}
    /* Six columns is already a lot on a phone: the season score is the one that
       can go, the form number and the table place carry the message. */
    td.season, th.season {{ display:none; }}
    /* Squeezed into a phone the full-width pitch would shrink the jerseys to a
       few pixels, so it keeps a readable width and scrolls sideways instead. */
    .pwrap {{ overflow-x:auto; }}
    .pwrap svg {{ min-width:700px; }}
    /* Handwriting needs more size than a grotesque to stay legible, so the
       badge gives up padding on a phone rather than point size. A narrow cell
       has no spare width beside the record, so the note hangs off the bottom
       left of the name block instead - still out of flow, still over the rule. */
    .badge {{ font-size:14px; padding:1px 10px 2px; left:30px; bottom:-9px;
              transform:rotate(-3.4deg); }}
    /* No free space beside the clubs at this width - the note covered a name -
       so it moves out into the date column, under the kick-off, and reads as
       written in the margin. */
    .fx .badge {{ left:-56px; right:auto; top:auto; bottom:-6px;
                  transform:rotate(-4deg); }}
    /* Less room, so the strokes reach further across the row. */
    tr.marked {{ background-size:82% 58%, 58% 38%; }}
    .tc {{ gap:7px; }}
    .lg {{ width:23px; height:23px; }}
    .tok {{ width:24px; height:24px; font-size:12.5px; }}
    .pw b {{ font-size:20px; }}
    /* The chart scales with the viewport, so its labels need bigger user units. */
    .tick {{ font-size:19px; }}
    /* No margin to write in on a phone: the note goes above its line instead. */
    .cb {{ padding:34px 0 8px; }}
    .cb .badge {{ left:4px; top:4px; }}
  }}
</style>
</head>
<body>
{snapshot_banner(season, matchday_n)}
<header class="mast">
  <div class="wrap">
    <div class="mhead">
      <div>
        <h1>Power Ranking</h1>
        <p class="tag">Wer gerade gut spielt – gemessen an den letzten fünf Spieltagen und
        daran, gegen wen. Die Tabelle zeigt die Saison, diese Seite den Moment.</p>
      </div>
      <p class="where">{html.escape(STAFFEL_NAME)}<br>Saison {season}<br>
      Nach <b>Spieltag {matchday}</b>, {last_date}</p>
    </div>
    {season_switcher(season)}
    {matchday_nav(season, matchday, season_max_matchday, matchday_n is not None)}
    <div class="facts">
      <p class="fact"><span class="k">Beste Form</span>
      <span class="t">{html.escape(top_team["team"])}</span>
      <span class="n pos">{num(top_team["form"])}</span></p>
      <p class="fact"><span class="k">Schwächste Form</span>
      <span class="t">{html.escape(bottom_team["team"])}</span>
      <span class="n neg">{num(bottom_team["form"])}</span></p>
      <p class="fact"><span class="k">Mannschaft der Stunde</span>
      {hot_fact}</p>
    </div>
  </div>
</header>
<main class="wrap">
{tabs_html}  <div class="view" id="v-form"{form_view_attrs}>
  <section class="pitchsec">
    <h2>Aufstellung</h2>
    <p class="sub">Ein Trikot je Team, aufgestellt nach der aktuellen Form.
    <strong>Die Nummer am Trikot ist der Platz in der Formtabelle.</strong></p>
    <div class="pitchcol">
      <div class="pends">
        <p class="pend"><i class="neg"></i>schwächer<b>{pitch_lo}</b></p>
        <p class="pend"><b>{pitch_hi}</b>stärker<i class="pos"></i></p>
      </div>
      <div class="pwrap">{pitch_svg}</div>
      <p class="phead">Links liegen die Teams unter dem Ligadurchschnitt, rechts davon die
      darüber.</p>
    </div>
  </section>

  <div class="dash">
    <div class="col">
      <section>
        <h2>Formtabelle</h2>
        <p class="sub">Sortiert nach den letzten fünf Spieltagen, nicht nach der Saison.
        Fünf Spiele beschreiben, was war – vorhersagen können sie nichts.</p>
        <div class="card rank">
          <table>
            <thead>
              <tr>
                <th>#</th><th class="l">Team</th><th class="l">Form</th>
                <th>+/&minus;</th><th class="l season">Saison</th>
                <th class="s-hide">Sp</th><th class="s-hide">Tore</th>
                <th class="s-hide">Diff</th>{rest_header}<th>Tabelle</th>
              </tr>
            </thead>
            <tbody>
              {chr(10).join("              " + r for r in body_rows).strip()}
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <div class="col chartcol">
      <section>
        {side_section}
        <p class="hint legend"><strong>Form</strong> zählt nur die letzten fünf Spieltage,
        <strong>Saison</strong> alle bisherigen – 50 ist jeweils Ligadurchschnitt. Der Wert hinter
        dem Tabellenplatz ist der Abstand zur Formtabelle: <span class="chip up">+2</span> heißt
        zwei Plätze besser als in der offiziellen Tabelle. Alle Spalten und Marker:
        <a href="#methodik">Erklärungen</a>.</p>
      </section>
    </div>
  </div>
  </div>
{other_views}  <footer>
    <p>Datenquelle: <a href="{SOURCE_URL}">fussball.de</a> (DFB) – dort stehen die offizielle
    Tabelle und alle Ergebnisse. Diese Seite zeigt nur daraus berechnete Werte.
    Privates, nicht-kommerzielles Projekt.{f" Stand der Berechnung: {generated}" if matchday_n is None else ""}</p>
  </footer>
</main>
<script>
  const panels = [...document.querySelectorAll('.tpanel')];
  const picker = document.getElementById('tpick');

  // The detail section follows the dropdown.
  function showDetail(rank) {{
    panels.forEach(p => {{
      p.hidden = p.dataset.rank !== rank;
      if (p.hidden) return;
      const seasonLine = p.querySelector('#season' + rank);
      if (seasonLine) {{
        p.querySelectorAll('.line').forEach(l => l.classList.remove('sel'));
        seasonLine.classList.add('sel');
        seasonLine.parentNode.appendChild(seasonLine);
      }}
    }});
  }}
  if (picker) {{
    picker.addEventListener('change', () => showDetail(picker.value));
    showDetail(picker.value);
  }}

  // On a phone the pitch is wider than the screen and scrolls. Left-aligned it
  // opens on the weakest teams with the halfway line off-screen, so it starts
  // centred on the league average instead. Redone whenever its tab opens,
  // because a hidden pitch has no width to centre on.
  const pw = document.querySelector('.pwrap');
  function centrePitch() {{ if (pw) pw.scrollLeft = (pw.scrollWidth - pw.clientWidth) / 2; }}

  // Tabs: the hash names the open one, so a view can be linked to directly.
  const tabs = [...document.querySelectorAll('.tabs button')];
  const views = [...document.querySelectorAll('.view')];
  function showView(id) {{
    tabs.forEach(b => b.setAttribute('aria-selected', String(b.dataset.view === id)));
    views.forEach(v => v.hidden = v.id !== 'v-' + id);
    centrePitch();
  }}
  if (tabs.length) {{
    tabs.forEach(b => b.addEventListener('click', () => {{
      showView(b.dataset.view);
      history.replaceState(null, '', '#' + b.dataset.view);
    }}));
    const start = location.hash.slice(1);
    showView(tabs.some(b => b.dataset.view === start) ? start : 'form');
    // In-page links to a tab (the notes point at #methodik) open it.
    window.addEventListener('hashchange', () => {{
      const id = location.hash.slice(1);
      if (!tabs.some(b => b.dataset.view === id)) return;
      showView(id);
      document.querySelector('.tabs').scrollIntoView();
    }});
  }} else {{
    centrePitch();
  }}
</script>
</body>
</html>
"""


def check_css(html):
    """The stylesheet is one long f-string, and a stray `*/` while editing a
    comment silently kills every rule after it - the page still renders, just
    wrong. Twice now. The committed HTML is the deployment, so refuse to write
    one rather than notice it in a screenshot."""
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    depth = i = 0
    while i < len(css):
        if css.startswith("/*", i):
            depth += 1
        elif css.startswith("*/", i):
            depth -= 1
            if depth < 0:
                raise ValueError(f"unopened CSS comment near: {css[max(0, i - 90):i + 2]!r}")
        else:
            i += 1
            continue
        i += 2
    if depth:
        raise ValueError("unclosed CSS comment")


def copy_assets():
    """Copy font and logo files to docs/assets/ so pages can reference them."""
    import shutil
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    assets_dir = os.path.join(docs_dir, "assets")

    for subdir in ("fonts", "logos"):
        src = os.path.join(os.path.dirname(__file__), "..", "assets", subdir)
        dst = os.path.join(assets_dir, subdir)
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(src):
                src_file = os.path.join(src, name)
                if os.path.isfile(src_file):
                    dst_file = os.path.join(dst, name)
                    shutil.copy2(src_file, dst_file)


def write_report(season, rows, path=None, matchday_n=None):
    """Write one season page.

    Args:
        season: season id
        rows: all rows from matches.csv
        path: override output path (if None, use season_path)
        matchday_n: if given, write a snapshot page for that matchday
    """
    if path is None:
        if matchday_n is not None:
            # Snapshot path: docs/2026-27/spieltag-05/index.html
            season_slug = season.replace("/", "-")
            docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs", season_slug)
            path = os.path.join(docs_dir, f"spieltag-{matchday_n:02d}", "index.html")
        else:
            path = season_path(season)

    html_out = render(season, rows, matchday_n)
    check_css(html_out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return path


def write_season(season, rows):
    """Write all pages for a season: current state + snapshots for each matchday.

    Also copies font and logo files to docs/assets/.
    """
    copy_assets()

    # Write current state (index.html or archive)
    current_path = write_report(season, rows)

    # Write snapshots for each matchday (current season only)
    if season == SEASON_CURRENT:
        played = [r for r in rows if r["status"] == "played"]
        if played:
            max_matchday = max(int(r["matchday"]) for r in played)
            for matchday_n in range(1, max_matchday + 1):
                write_report(season, rows, matchday_n=matchday_n)

    return current_path


if __name__ == "__main__":
    import argparse
    import csv

    parser = argparse.ArgumentParser(description="Rebuild docs/ pages from matches.csv")
    parser.add_argument("--season", default=SEASON_CURRENT)
    parser.add_argument("--matchday", type=int, help="Rebuild only one matchday snapshot")
    args = parser.parse_args()

    with open(MATCHES_CSV, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["season"] == args.season]

    if args.matchday:
        print(write_report(args.season, rows, matchday_n=args.matchday))
    else:
        print(write_season(args.season, rows))
